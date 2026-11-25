import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "credentials, starts",
    [
        ({}, False),
        ({"SERVICENOW_USERNAME": "agent"}, False),
        ({"SERVICENOW_PASSWORD": "password"}, False),
        ({"SERVICENOW_API_KEY": " ", "SERVICENOW_USERNAME": "agent", "SERVICENOW_PASSWORD": " "}, False),
        ({"SERVICENOW_API_KEY": "test-key"}, True),
        ({"SERVICENOW_USERNAME": "agent", "SERVICENOW_PASSWORD": "password"}, True),
    ],
)
def test_mcp_app_requires_explicit_credentials(credentials, starts):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("SERVICENOW_", "MCP_"))}
    env.update(SERVICENOW_URL="https://instance.example.test", **credentials)
    result = subprocess.run(
        [sys.executable, "-c", "from mcp_servicenow import app"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    if starts:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "ServiceNow credentials required" in result.stderr
