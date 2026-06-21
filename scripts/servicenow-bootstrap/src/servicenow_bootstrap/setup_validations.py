#!/usr/bin/env python3
"""
ServiceNow Incident API Validation Script

Tests that the NOC Agent credentials can perform full incident CRUD
against the ServiceNow Table API. Used as a post-bootstrap health check
and in CI pipelines.
"""

import sys
from typing import Any, Dict, Optional, Tuple

import requests

from .servicenow_client import ServiceNowClient


class ServiceNowIncidentTester(ServiceNowClient):
    """Validate incident table CRUD using NOC Agent credentials."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        super().__init__(username=username, password=password)
        self._created_sys_id: Optional[str] = None
        self._created_number: Optional[str] = None

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Make HTTP request to ServiceNow API.

        Returns:
            Tuple of (success, response_data, error_message)
        """
        url = f"{self.instance_url}/api/now/{endpoint}"

        try:
            response = self.session.request(method.upper(), url, json=data)

            if 200 <= response.status_code < 300:
                try:
                    return True, response.json(), ""
                except ValueError:
                    return True, {"raw_response": response.text}, ""
            else:
                return False, {}, f"HTTP {response.status_code}: {response.text[:300]}"

        except requests.exceptions.RequestException as e:
            return False, {}, str(e)

    def test_create_incident(self) -> Tuple[bool, str]:
        """Test POST /api/now/table/incident."""
        print("Testing incident CREATE...")

        payload = {
            "short_description": "[Validation] Bootstrap test incident",
            "description": (
                "Automated validation from servicenow-bootstrap. "
                "This incident will be resolved and can be safely deleted."
            ),
            "priority": "4",
            "category": "Infrastructure",
            "subcategory": "OpenShift",
            "assignment_group": "NOC-Team",
            "state": "1",
            "urgency": "4",
            "impact": "4",
        }

        success, data, error = self._make_request("POST", "table/incident", payload)

        if success:
            result = data.get("result", {})
            self._created_sys_id = result.get("sys_id", "")
            self._created_number = result.get("number", "")
            return (
                True,
                f"  CREATE passed — {self._created_number} " f"(sys_id: {self._created_sys_id})",
            )
        else:
            return False, f"  CREATE failed — {error}"

    def test_read_incident(self) -> Tuple[bool, str]:
        """Test GET /api/now/table/incident by number."""
        if not self._created_number:
            return False, "  READ skipped — no incident to read (create failed)"

        print("Testing incident READ...")

        success, data, error = self._make_request(
            "GET",
            f"table/incident?sysparm_query=number={self._created_number}" f"&sysparm_limit=1",
        )

        if success:
            results = data.get("result", [])
            if results:
                desc = results[0].get("short_description", "")
                return True, f"  READ passed — found '{desc}'"
            else:
                return (
                    False,
                    f"  READ failed — incident {self._created_number} not found in results",
                )
        else:
            return False, f"  READ failed — {error}"

    def test_update_incident(self) -> Tuple[bool, str]:
        """Test PATCH /api/now/table/incident/{sys_id}."""
        if not self._created_sys_id:
            return False, "  UPDATE skipped — no incident to update (create failed)"

        print("Testing incident UPDATE...")

        payload = {
            "work_notes": "Validation: update test from servicenow-bootstrap",
            "state": "2",
        }

        success, data, error = self._make_request("PATCH", f"table/incident/{self._created_sys_id}", payload)

        if success:
            return True, "  UPDATE passed — added work notes, moved to In Progress"
        else:
            return False, f"  UPDATE failed — {error}"

    def test_resolve_incident(self) -> Tuple[bool, str]:
        """Test resolving an incident via PATCH."""
        if not self._created_sys_id:
            return False, "  RESOLVE skipped — no incident to resolve (create failed)"

        print("Testing incident RESOLVE...")

        payload = {
            "state": "6",
            "close_code": "Solved (Permanently)",
            "close_notes": "Validation: resolved by servicenow-bootstrap",
        }

        success, data, error = self._make_request("PATCH", f"table/incident/{self._created_sys_id}", payload)

        if success:
            return True, "  RESOLVE passed — incident marked as Resolved"
        else:
            return False, f"  RESOLVE failed — {error}"

    def test_caller_resolution(self) -> Tuple[bool, str]:
        """Test that caller lookup works via sys_user table."""
        print("Testing caller resolution via sys_user table...")

        success, data, error = self._make_request(
            "GET",
            "table/sys_user?sysparm_query=name=NOC Agent" "&sysparm_limit=1&sysparm_fields=sys_id,name,user_name",
        )

        if success:
            results = data.get("result", [])
            if results:
                user = results[0]
                return (
                    True,
                    f"  CALLER passed — found '{user.get('name')}' " f"(user_name: {user.get('user_name')})",
                )
            else:
                return False, "  CALLER failed — 'NOC Agent' user not found in sys_user"
        else:
            return False, f"  CALLER failed — {error}"

    def _cleanup_incident(self) -> None:
        """Delete the validation incident so repeated runs don't accumulate."""
        if not self._created_sys_id:
            return
        print("Cleaning up validation incident...")
        success, _, error = self._make_request("DELETE", f"table/incident/{self._created_sys_id}")
        if success:
            print(f"  Deleted {self._created_number}")
        else:
            print(f"  Cleanup failed (non-fatal): {error}")

    def run_all_tests(self) -> Dict[str, Tuple[bool, str]]:
        """Run the full incident CRUD validation suite."""
        print("=" * 60)
        print("ServiceNow Incident API Validation")
        print("=" * 60)
        print(f"Instance : {self.instance_url}")
        print(f"Username : {self.username}")
        print("=" * 60)
        print()

        tests = [
            ("Create Incident", self.test_create_incident),
            ("Read Incident", self.test_read_incident),
            ("Update Incident", self.test_update_incident),
            ("Resolve Incident", self.test_resolve_incident),
            ("Caller Resolution", self.test_caller_resolution),
        ]

        results: Dict[str, Tuple[bool, str]] = {}
        passed = 0
        total = len(tests)

        for test_name, test_func in tests:
            try:
                success, message = test_func()
                results[test_name] = (success, message)
                print(message)
                if success:
                    passed += 1
            except Exception as e:
                error_msg = f"  {test_name} failed with exception: {e}"
                results[test_name] = (False, error_msg)
                print(error_msg)
            print()

        print("=" * 60)
        print("Results Summary")
        print("=" * 60)
        print(f"Passed: {passed}/{total}")

        if passed == total:
            print("\nAll tests passed! Your ServiceNow instance is ready for " "incident management.")
            print("\nYou can now deploy with a real ServiceNow instance:")
            print(
                f"  SERVICENOW_URL={self.instance_url}\n"
                f"  SERVICENOW_MODE=real\n"
                f"  SERVICENOW_USERNAME={self.username}\n"
                f"  SERVICENOW_PASSWORD=<your-password>"
            )
        elif passed > 0:
            print("\nSome tests passed. Check the failed tests above.")
        else:
            print("\nAll tests failed. Verify credentials, roles (itil, rest_service), " "and API access policies.")

        self._cleanup_incident()

        return results


def main() -> None:
    """Main entry point for the validation script."""
    try:
        tester = ServiceNowIncidentTester()
        results = tester.run_all_tests()

        failed = [name for name, (ok, _) in results.items() if not ok]
        sys.exit(1 if failed else 0)

    except KeyboardInterrupt:
        print("\nValidation interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
