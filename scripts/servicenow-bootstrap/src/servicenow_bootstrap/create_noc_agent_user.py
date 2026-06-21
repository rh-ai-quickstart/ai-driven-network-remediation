#!/usr/bin/env python3
"""
ServiceNow User Automation Script

Creates and configures the NOC Agent user for ServiceNow incident management.
Adapted from the it-self-service-agent bootstrap pattern, scoped to the
itil + rest_service roles needed for incident table CRUD.
"""

import argparse
import json
import os
import secrets
import string
import sys
from typing import Any, Dict

import requests

from .servicenow_client import ServiceNowClient

CREDS_FILE = ".servicenow-creds.json"


class ServiceNowUserAutomation(ServiceNowClient):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.agent_config = config["servicenow"]["agent_user"]

    def generate_password(self, length: int = 16) -> str:
        """Generate a secure random password."""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def check_user_exists(self, user_id: str) -> bool:
        """Check if user already exists.

        Raises on connectivity/auth errors so they aren't masked as
        'user not found'.
        """
        url = f"{self.instance_url}/api/now/table/sys_user"
        params = {"sysparm_query": f"user_name={user_id}"}

        response = self.session.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return len(data.get("result", [])) > 0

    def create_user(self) -> Dict[str, str]:
        """Create the NOC Agent user."""
        user_id = self.agent_config["user_id"]

        if self.check_user_exists(user_id):
            print(f"User '{user_id}' already exists. Skipping creation.")
            return {"user_id": user_id, "password": "existing_user"}

        password = self.generate_password()

        user_data = {
            "user_name": user_id,
            "first_name": self.agent_config["first_name"],
            "last_name": self.agent_config["last_name"],
            "user_password": password,
            "password_needs_reset": "false",
            "active": "true",
            "locked_out": "false",
            "identity_type": self.agent_config["identity_type"],
        }

        url = f"{self.instance_url}/api/now/table/sys_user"

        try:
            response = self.session.post(url, json=user_data)
            response.raise_for_status()

            result = response.json()
            sys_id = result["result"]["sys_id"]
            print(f"User '{user_id}' created successfully!")

            creds = {
                "user_id": user_id,
                "password": password,
                "sys_id": sys_id,
                "instance_url": self.instance_url,
            }
            self._write_creds_file(creds)

            return {
                "user_id": user_id,
                "password": password,
                "sys_id": sys_id,
            }

        except requests.RequestException as e:
            print(f"Error creating user: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            raise

    @staticmethod
    def _write_creds_file(creds: Dict[str, str]) -> None:
        """Write credentials to a gitignored file with restricted permissions."""
        fd = os.open(CREDS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(creds, f, indent=2)
                f.write("\n")
        except Exception:
            os.close(fd)
            raise
        print(f"Credentials written to {CREDS_FILE} (mode 0600)")

    def assign_roles(self, user_sys_id: str) -> None:
        """Assign configured roles to the user."""
        roles_to_assign = self.agent_config["roles_to_assign"]

        for role_name in roles_to_assign:
            try:
                role_url = f"{self.instance_url}/api/now/table/sys_user_role"
                role_params = {
                    "sysparm_query": f"name={role_name}",
                    "sysparm_fields": "sys_id",
                }

                role_response = self.session.get(role_url, params=role_params)
                role_response.raise_for_status()
                role_data = role_response.json()

                if not role_data.get("result"):
                    print(f"Role '{role_name}' not found, skipping...")
                    continue

                role_sys_id = role_data["result"][0]["sys_id"]

                has_role_url = f"{self.instance_url}/api/now/table/sys_user_has_role"
                has_role_params = {
                    "sysparm_query": f"user={user_sys_id}^role={role_sys_id}",
                    "sysparm_fields": "sys_id",
                }

                has_role_response = self.session.get(has_role_url, params=has_role_params)
                has_role_response.raise_for_status()
                has_role_data = has_role_response.json()

                if has_role_data.get("result"):
                    print(f"User already has role '{role_name}'")
                    continue

                assignment_data = {"user": user_sys_id, "role": role_sys_id}

                assignment_url = f"{self.instance_url}/api/now/table/sys_user_has_role"
                assignment_response = self.session.post(assignment_url, json=assignment_data)
                assignment_response.raise_for_status()

                print(f"Assigned role '{role_name}' to user")

            except requests.RequestException as e:
                print(f"Error assigning role '{role_name}': {e}")
                continue

    def setup_user(self) -> Dict[str, str]:
        """Complete user setup process."""
        print("Starting user setup...")

        user_info = self.create_user()

        if user_info["password"] != "existing_user":
            if "sys_id" not in user_info:
                user_info["sys_id"] = self.get_user_sys_id(user_info["user_id"])

            print("Assigning roles...")
            self.assign_roles(user_info["sys_id"])
        else:
            print("User already exists, verifying roles...")
            user_sys_id = self.get_user_sys_id(user_info["user_id"])
            self.assign_roles(user_sys_id)

        print("User setup completed!")
        return user_info


def main() -> None:
    parser = argparse.ArgumentParser(description="Automate ServiceNow user creation")
    parser.add_argument("--config", required=True, help="Path to configuration file")
    args = parser.parse_args()

    try:
        with open(args.config, "r") as f:
            config = json.load(f)

        automation = ServiceNowUserAutomation(config)
        automation.setup_user()

    except FileNotFoundError:
        print(f"Configuration file not found: {args.config}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Invalid JSON in configuration file: {args.config}")
        sys.exit(1)


if __name__ == "__main__":
    main()
