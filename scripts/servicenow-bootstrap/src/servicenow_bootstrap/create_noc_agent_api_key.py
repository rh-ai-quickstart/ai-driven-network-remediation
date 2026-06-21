#!/usr/bin/env python3
"""
ServiceNow API Configuration Automation Script

Creates API keys, authentication profiles, and access policies for the
NOC Agent's ServiceNow integration. Focused on Table API access needed
for incident management.
"""

import argparse
import json
import sys
from typing import Any, Dict

import requests

from .servicenow_client import ServiceNowClient


class ServiceNowAPIAutomation(ServiceNowClient):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.agent_user_id = config["servicenow"]["agent_user"]["user_id"]
        self.api_key_name = config["servicenow"]["api_key_name"]

    def create_api_key(self) -> Dict[str, str]:
        """Create API key for the NOC agent user."""
        print("Creating API key...")

        check_url = f"{self.instance_url}/api/now/table/api_key"
        check_params = {"sysparm_query": f"name={self.api_key_name}"}

        try:
            response = self.session.get(check_url, params=check_params)
            response.raise_for_status()
            data = response.json()

            if data.get("result"):
                print(f"API key '{self.api_key_name}' already exists")
                api_key_record = data["result"][0]
                return {
                    "api_key_sys_id": api_key_record["sys_id"],
                    "token": api_key_record.get("token", "hidden"),
                }

            user_sys_id = self.get_user_sys_id(self.agent_user_id)

            api_key_data = {
                "name": self.api_key_name,
                "user": user_sys_id,
                "active": "true",
            }

            create_url = f"{self.instance_url}/api/now/table/api_key"
            response = self.session.post(create_url, json=api_key_data)
            response.raise_for_status()

            result = response.json()
            api_key_info = result["result"]

            print(f"API key '{self.api_key_name}' created successfully!")
            print(
                "API Key Token: log into your ServiceNow instance -> All -> "
                "Search for 'REST API Key' to retrieve the token"
            )
            print("Please save this token securely!")

            return {
                "api_key_sys_id": api_key_info["sys_id"],
                "token": api_key_info.get("token", "hidden"),
            }

        except requests.RequestException as e:
            print(f"Error creating API key: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            raise

    def create_auth_profile(self, name: str, auth_type: str) -> str:
        """Create authentication profile."""
        print(f"Creating authentication profile: {name}")

        check_url = f"{self.instance_url}/api/now/table/inbound_auth_profile"
        check_params = {"sysparm_query": f"name={name}"}

        try:
            response = self.session.get(check_url, params=check_params)
            response.raise_for_status()
            data = response.json()

            if data.get("result"):
                print(f"Authentication profile '{name}' already exists")
                return str(data["result"][0]["sys_id"])

            if auth_type == "api_key":
                table = "http_key_auth"
                profile_data = {"name": name, "auth_parameter": "Header for API Key"}
            else:
                table = "std_http_auth"
                profile_data = {"name": name, "type": "basic_auth"}

            create_url = f"{self.instance_url}/api/now/table/{table}"
            response = self.session.post(create_url, json=profile_data)
            response.raise_for_status()

            result = response.json()
            profile_sys_id = str(result["result"]["sys_id"])

            print(f"Authentication profile '{name}' created successfully!")
            return profile_sys_id

        except requests.RequestException as e:
            print(f"Error creating authentication profile '{name}': {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            raise

    def create_api_access_policy(self, policy_name: str, api_name: str, auth_profiles: Dict[str, str]) -> str:
        """Create API access policy."""
        print(f"Creating API access policy: {policy_name}")

        check_url = f"{self.instance_url}/api/now/table/sys_api_access_policy"
        check_params = {"sysparm_query": f"name={policy_name}"}

        try:
            response = self.session.get(check_url, params=check_params)
            response.raise_for_status()
            data = response.json()

            if data.get("result"):
                print(f"API access policy '{policy_name}' already exists")
                return str(data["result"][0]["sys_id"])

            policy_data = {
                "name": policy_name,
                "active": "true",
                "apply_all_methods": "true",
                "apply_all_resources": "true",
                "apply_all_versions": "true",
            }

            # ServiceNow may ignore the "api" field via API; manual step documented
            policy_data["api"] = api_name

            if "Table" in api_name:
                policy_data["api_path"] = "now/table"

            create_url = f"{self.instance_url}/api/now/table/sys_api_access_policy"
            response = self.session.post(create_url, json=policy_data)
            response.raise_for_status()

            result = response.json()
            policy_sys_id = str(result["result"]["sys_id"])

            print(f"API access policy '{policy_name}' created successfully!")

            self.create_auth_profile_mapping(policy_sys_id, auth_profiles)

            return policy_sys_id

        except requests.RequestException as e:
            print(f"Error creating API access policy '{policy_name}': {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            raise

    def create_auth_profile_mapping(self, policy_sys_id: str, auth_profiles: Dict[str, str]) -> None:
        """Create authentication profile mappings for the API access policy."""
        print("Creating authentication profile mappings...")

        mapping_url = f"{self.instance_url}/api/now/table/sys_auth_profile_mapping"

        if "basic_auth" in auth_profiles:
            self._create_single_auth_mapping(mapping_url, policy_sys_id, "basic_auth", auth_profiles["basic_auth"])

        for auth_type, profile_sys_id in auth_profiles.items():
            if auth_type != "basic_auth":
                self._create_single_auth_mapping(mapping_url, policy_sys_id, auth_type, profile_sys_id)

    def _create_single_auth_mapping(
        self,
        mapping_url: str,
        policy_sys_id: str,
        auth_type: str,
        profile_sys_id: str,
    ) -> None:
        """Helper to create a single auth profile mapping."""
        try:
            payload = {
                "api_access_policy": policy_sys_id,
                "inbound_auth_profile": profile_sys_id,
            }

            response = self.session.post(mapping_url, json=payload)
            response.raise_for_status()

            print(f"{auth_type.replace('_', ' ').title()} authentication profile " "mapping created successfully!")

        except requests.RequestException as e:
            print(f"Error creating {auth_type} auth profile mapping: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            raise

    def setup_api_configuration(self) -> Dict[str, Any]:
        """Complete API configuration setup — focused on Table API access."""
        print("Starting API configuration setup...")

        results: Dict[str, Any] = {}

        api_key_info = self.create_api_key()
        results["api_key"] = api_key_info

        api_key_profile_sys_id = self.create_auth_profile("API Key", "api_key")
        basic_auth_profile_sys_id = self.create_auth_profile("Basic Auth", "basic")

        results["auth_profiles"] = {
            "api_key": api_key_profile_sys_id,
            "basic_auth": basic_auth_profile_sys_id,
        }

        table_policy_sys_id = self.create_api_access_policy("NOC Agent - Tables", "Table API", results["auth_profiles"])
        results["table_policy"] = table_policy_sys_id

        print("API configuration setup completed!")
        print(
            "\nNOTE: You may need to manually set the REST API dropdown on the access "
            "policy in the ServiceNow UI. Navigate to All -> REST API Access Policies "
            "-> 'NOC Agent - Tables' and set the API field to 'Table API'."
        )
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Automate ServiceNow API configuration")
    parser.add_argument("--config", required=True, help="Path to configuration file")
    args = parser.parse_args()

    try:
        with open(args.config, "r") as f:
            config = json.load(f)

        automation = ServiceNowAPIAutomation(config)
        automation.setup_api_configuration()

    except FileNotFoundError:
        print(f"Configuration file not found: {args.config}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Invalid JSON in configuration file: {args.config}")
        sys.exit(1)


if __name__ == "__main__":
    main()
