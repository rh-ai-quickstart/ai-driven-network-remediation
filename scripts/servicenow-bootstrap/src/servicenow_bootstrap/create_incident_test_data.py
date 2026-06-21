#!/usr/bin/env python3
"""
ServiceNow Incident Test Data Automation Script

Creates assignment groups and a sample incident to verify the NOC Agent's
incident management setup works correctly. Specific to the AI-driven
network remediation quickstart.
"""

import argparse
import json
import sys
from typing import Any, Dict, List

import requests

from .servicenow_client import ServiceNowClient


class ServiceNowIncidentDataAutomation(ServiceNowClient):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.incident_config = config.get("incident", {})
        self.caller_name = config.get("servicenow", {}).get("caller_name", "NOC Agent")

    def _group_exists(self, group_name: str) -> bool:
        """Check if a sys_user_group already exists by name.

        Raises on connectivity/auth errors so they aren't masked as
        'group not found'.
        """
        url = f"{self.instance_url}/api/now/table/sys_user_group"
        params = {
            "sysparm_query": f"name={group_name}",
            "sysparm_fields": "sys_id",
        }

        response = self.session.get(url, params=params)
        response.raise_for_status()
        return len(response.json().get("result", [])) > 0

    def create_assignment_groups(self) -> List[Dict[str, str]]:
        """Create assignment groups from config."""
        groups = self.incident_config.get("assignment_groups", [])
        created: List[Dict[str, str]] = []

        for group_name in groups:
            if self._group_exists(group_name):
                print(f"Assignment group '{group_name}' already exists, skipping")
                created.append({"name": group_name, "status": "exists"})
                continue

            url = f"{self.instance_url}/api/now/table/sys_user_group"
            group_data = {
                "name": group_name,
                "description": (f"Auto-created by servicenow-bootstrap for " f"AI-driven network remediation"),
                "active": "true",
            }

            try:
                response = self.session.post(url, json=group_data)
                response.raise_for_status()

                result = response.json()["result"]
                print(f"Assignment group '{group_name}' created (sys_id: {result['sys_id']})")
                created.append(
                    {
                        "name": group_name,
                        "sys_id": result["sys_id"],
                        "status": "created",
                    }
                )

            except requests.RequestException as e:
                print(f"Error creating group '{group_name}': {e}")
                created.append({"name": group_name, "status": "error"})

        return created

    def _resolve_caller_sys_id(self) -> str:
        """Find the NOC Agent caller user sys_id."""
        url = f"{self.instance_url}/api/now/table/sys_user"
        params = {
            "sysparm_query": f"name={self.caller_name}",
            "sysparm_fields": "sys_id,name",
            "sysparm_limit": "1",
        }

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            results = response.json().get("result", [])
            if results:
                return results[0]["sys_id"]
        except requests.RequestException:
            pass

        return ""

    _SAMPLE_SHORT_DESC = "[Bootstrap Test] Edge cluster nginx OOMKilled — auto-remediation " "validation"

    def _sample_incident_exists(self) -> bool:
        """Check if the bootstrap sample incident already exists.

        Raises on connectivity/auth errors so they aren't masked.
        """
        url = f"{self.instance_url}/api/now/table/incident"
        params = {
            "sysparm_query": f"short_description={self._SAMPLE_SHORT_DESC}",
            "sysparm_fields": "sys_id",
            "sysparm_limit": "1",
        }
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return len(response.json().get("result", [])) > 0

    def create_sample_incident(self) -> Dict[str, Any]:
        """Create a sample incident to verify the setup works.

        Idempotent — skips creation if the bootstrap test incident
        already exists.
        """
        print("Creating sample incident...")

        if self._sample_incident_exists():
            print("Sample bootstrap incident already exists, skipping")
            return {"status": "exists"}

        categories = self.incident_config.get("categories", {})
        category = next(iter(categories), "Infrastructure")
        subcategories = categories.get(category, ["OpenShift"])
        subcategory = subcategories[0] if subcategories else "OpenShift"

        groups = self.incident_config.get("assignment_groups", ["NOC-Team"])
        assignment_group = groups[0] if groups else "NOC-Team"

        caller_sys_id = self._resolve_caller_sys_id()

        payload: Dict[str, Any] = {
            "short_description": self._SAMPLE_SHORT_DESC,
            "description": (
                "This is a test incident created by the servicenow-bootstrap script "
                "to validate that the NOC Agent user can create, read, update, and "
                "resolve incidents via the Table API.\n\n"
                "Simulated scenario: nginx pod on edge cluster 'edge-01' was "
                "OOMKilled due to memory limit 32Mi. AI agent detected the failure "
                "via Kafka log stream, analyzed with Granite, and initiated "
                "auto-remediation via AAP."
            ),
            "priority": "3",
            "category": category,
            "subcategory": subcategory,
            "assignment_group": assignment_group,
            "state": "1",
            "urgency": "3",
            "impact": "3",
        }

        if caller_sys_id:
            payload["caller_id"] = caller_sys_id
        else:
            payload["caller_id"] = self.caller_name

        url = f"{self.instance_url}/api/now/table/incident"

        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()

            data = response.json()["result"]
            ticket_number = data.get("number", "")
            sys_id = data.get("sys_id", "")

            print(f"Sample incident created: {ticket_number} (sys_id: {sys_id})")
            return {
                "ticket_number": ticket_number,
                "sys_id": sys_id,
                "status": "created",
            }

        except requests.RequestException as e:
            print(f"Error creating sample incident: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            return {"status": "error", "error": str(e)}

    def setup_incident_data(self) -> Dict[str, Any]:
        """Complete incident test data setup."""
        print("Starting incident test data setup...")

        results: Dict[str, Any] = {}

        print("\n--- Creating assignment groups ---")
        results["assignment_groups"] = self.create_assignment_groups()

        print("\n--- Creating sample incident ---")
        results["sample_incident"] = self.create_sample_incident()

        print("\nIncident test data setup completed!")
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Create ServiceNow incident test data")
    parser.add_argument("--config", required=True, help="Path to configuration file")
    args = parser.parse_args()

    try:
        with open(args.config, "r") as f:
            config = json.load(f)

        automation = ServiceNowIncidentDataAutomation(config)
        automation.setup_incident_data()

    except FileNotFoundError:
        print(f"Configuration file not found: {args.config}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Invalid JSON in configuration file: {args.config}")
        sys.exit(1)


if __name__ == "__main__":
    main()
