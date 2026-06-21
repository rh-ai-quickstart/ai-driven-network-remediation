#!/usr/bin/env python3
"""
ServiceNow PDI Setup Orchestration Script

Orchestrates the complete ServiceNow PDI setup for the AI-driven network
remediation quickstart. Runs user creation, API key setup, incident test data,
and validation in sequence.
"""

import argparse
import json
import sys
from typing import Any, Dict

from .create_incident_test_data import ServiceNowIncidentDataAutomation
from .create_noc_agent_api_key import ServiceNowAPIAutomation
from .create_noc_agent_user import ServiceNowUserAutomation
from .setup_validations import ServiceNowIncidentTester
from .utils import get_env_var


def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration from JSON file and inject environment variables."""
    try:
        with open(config_path, "r") as f:
            config: Dict[str, Any] = json.load(f)

        config["servicenow"]["instance_url"] = get_env_var("SERVICENOW_INSTANCE_URL")
        config["servicenow"]["admin_username"] = get_env_var("SERVICENOW_USERNAME")
        config["servicenow"]["admin_password"] = get_env_var("SERVICENOW_PASSWORD")

        return config
    except FileNotFoundError:
        print(f"Configuration file not found: {config_path}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Invalid JSON in configuration file: {config_path}")
        sys.exit(1)


def validate_config(config: Dict[str, Any]) -> bool:
    """Validate that required configuration fields are present."""
    required_fields = [
        "servicenow.agent_user.user_id",
        "servicenow.agent_user.first_name",
        "servicenow.agent_user.last_name",
        "servicenow.api_key_name",
        "incident.assignment_groups",
    ]

    missing_fields = []

    for field in required_fields:
        keys = field.split(".")
        current: Any = config

        try:
            for key in keys:
                current = current[key]
        except (KeyError, TypeError):
            missing_fields.append(field)

    if missing_fields:
        print("Missing required configuration fields:")
        for field in missing_fields:
            print(f"   - {field}")
        return False

    return True


def print_step(step_num: int, step_name: str) -> None:
    """Print step header."""
    print(f"\n{'=' * 50}")
    print(f"Step {step_num}: {step_name}")
    print(f"{'=' * 50}")


def confirm_proceed(message: str) -> bool:
    """Ask user for confirmation before proceeding."""
    while True:
        response = input(f"{message} (y/n): ").lower().strip()
        if response in ("y", "yes"):
            return True
        elif response in ("n", "no"):
            return False
        else:
            print("Please enter 'y' or 'n'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Complete ServiceNow PDI setup for network remediation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m servicenow_bootstrap.orchestrator --config config.json
  python -m servicenow_bootstrap.orchestrator --config config.json --skip-user
  python -m servicenow_bootstrap.orchestrator --config config.json --skip-validation
        """,
    )

    parser.add_argument("--config", required=True, help="Path to configuration file")
    parser.add_argument("--skip-user", action="store_true", help="Skip user creation step")
    parser.add_argument("--skip-api", action="store_true", help="Skip API configuration step")
    parser.add_argument("--skip-data", action="store_true", help="Skip incident test data step")
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip validation step",
    )
    parser.add_argument("--no-confirm", action="store_true", help="Skip confirmation prompts")

    args = parser.parse_args()

    print("=" * 60)
    print("ServiceNow PDI Setup — AI-driven Network Remediation")
    print("=" * 60)
    print()

    print("Loading configuration...")
    config = load_config(args.config)

    if not validate_config(config):
        print("\nConfiguration validation failed. Please check your config file.")
        sys.exit(1)

    print("Configuration loaded and validated!")

    print(f"\nTarget instance : {config['servicenow']['instance_url']}")
    print(f"Admin user      : {config['servicenow']['admin_username']}")
    print(f"Agent user      : {config['servicenow']['agent_user']['user_id']}")
    print(f"Assignment groups: " f"{', '.join(config['incident']['assignment_groups'])}")

    steps_to_run = []
    if not args.skip_user:
        steps_to_run.append("Create NOC Agent user")
    if not args.skip_api:
        steps_to_run.append("Configure API keys and authentication")
    if not args.skip_data:
        steps_to_run.append("Create incident test data (groups + sample)")
    if not args.skip_validation:
        steps_to_run.append("Validate incident CRUD")

    if not steps_to_run:
        print("\nAll steps are being skipped. Nothing to do!")
        sys.exit(0)

    print("\nSteps to execute:")
    for i, step in enumerate(steps_to_run, 1):
        print(f"   {i}. {step}")

    if not args.no_confirm:
        if not confirm_proceed("\nProceed with setup?"):
            print("Setup cancelled by user.")
            sys.exit(0)

    print("\nStarting setup process...\n")

    results: Dict[str, Any] = {}

    try:
        # Step 1: Create user
        if not args.skip_user:
            print_step(1, "Create NOC Agent User")
            user_automation = ServiceNowUserAutomation(config)
            user_results = user_automation.setup_user()
            results["user"] = user_results

        # Step 2: Configure API
        if not args.skip_api:
            print_step(2, "Configure API Keys and Authentication")
            api_automation = ServiceNowAPIAutomation(config)
            api_results = api_automation.setup_api_configuration()
            results["api"] = api_results

        # Step 3: Create incident test data
        if not args.skip_data:
            print_step(3, "Create Incident Test Data")
            data_automation = ServiceNowIncidentDataAutomation(config)
            data_results = data_automation.setup_incident_data()
            results["incident_data"] = data_results

        # Step 4: Validate (as the noc_agent user, not admin)
        if not args.skip_validation:
            print_step(4, "Validate Incident CRUD")
            user_result = results.get("user", {})
            agent_user = user_result.get("user_id")
            agent_pass = user_result.get("password")
            if agent_user and agent_pass and agent_pass != "existing_user":
                print(f"Validating as '{agent_user}' (newly created agent)...")
                tester = ServiceNowIncidentTester(username=agent_user, password=agent_pass)
            else:
                print("Validating with credentials from environment...")
                tester = ServiceNowIncidentTester()
            validation_results = tester.run_all_tests()
            results["validation"] = validation_results

        # Print final summary
        print("\n" + "=" * 60)
        print("Setup completed!")
        print("=" * 60)

        if results.get("user"):
            user_id = config["servicenow"]["agent_user"]["user_id"]
            print(f"  User created: {user_id}")
            if results["user"].get("password") not in (None, "existing_user"):
                from .create_noc_agent_user import CREDS_FILE

                print(f"  Credentials: see {CREDS_FILE}")

        if results.get("api", {}).get("api_key"):
            print(f"  API Key: {config['servicenow']['api_key_name']}")
            print("  Token: log into ServiceNow -> All -> Search 'REST API Key' " "to retrieve it")

        if results.get("incident_data", {}).get("sample_incident"):
            inc = results["incident_data"]["sample_incident"]
            if inc.get("ticket_number"):
                print(f"  Sample incident: {inc['ticket_number']}")

        if results.get("validation"):
            failed = [n for n, (ok, _) in results["validation"].items() if not ok]
            if not failed:
                print("  Validation: all tests passed")
            else:
                print(f"  Validation: {len(failed)} test(s) failed")

        print("\nNext steps:")
        print("  1. Verify the setup in your ServiceNow instance")
        print("  2. Retrieve the API key token from the ServiceNow UI")
        print(
            "  3. Set the REST API dropdown on 'NOC Agent - Tables' access " "policy to 'Table API' (if not auto-set)"
        )
        print(
            "  4. Deploy with: make helm-install "
            'HELM_EXTRA_ARGS="--set mcp-servers.mcp-servers.'
            'noc-servicenow.env.SERVICENOW_MODE=real ..."'
        )

    except KeyboardInterrupt:
        print("\n\nSetup interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nSetup failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
