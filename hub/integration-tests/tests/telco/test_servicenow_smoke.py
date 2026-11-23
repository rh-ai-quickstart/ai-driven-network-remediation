"""Exercise the shared ServiceNow toolgroup via LlamaStack."""

import json
import os
from uuid import uuid4

import httpx
import pytest

from tests.conftest import _wait_for_health


@pytest.fixture
def mock_incident():
    url = os.environ.get("SERVICENOW_MOCK_URL")
    if not url:
        pytest.skip("Set SERVICENOW_MOCK_URL to run against the deployed mock")

    _wait_for_health(url, "/healthz", "servicenow-mock")
    api_key = os.environ.get("SERVICENOW_MOCK_API_KEY")
    headers = {"x-sn-apikey": api_key} if api_key else {}
    auth = (
        None
        if api_key
        else (
            os.environ.get("SERVICENOW_MOCK_USERNAME", "admin"),
            os.environ.get("SERVICENOW_MOCK_PASSWORD", "admin"),
        )
    )
    with httpx.Client(base_url=url, auth=auth, headers=headers) as mock:
        assert "incidents_count" in mock.get("/healthz").json()
        response = mock.post(
            "/api/now/table/incident",
            json={"short_description": f"Telco ServiceNow smoke test {uuid4()}"},
        )
        response.raise_for_status()
        return response.json()["result"]


def test_telco_hub_can_read_a_servicenow_incident(mock_incident, llamastack_client):
    response = llamastack_client.get("/v1/toolgroups")
    response.raise_for_status()
    groups = response.json()
    (servicenow,) = [
        group
        for group in groups.get("data", groups)
        if "mcp::noc-servicenow" in (group.get("identifier"), group.get("provider_resource_id"))
    ]
    assert servicenow["provider_id"] == "model-context-protocol"
    assert servicenow["mcp_endpoint"]["uri"] == "http://mcp-noc-servicenow:8000/mcp"

    # Listing the tools populates LlamaStack's routing cache before invocation.
    llamastack_client.get("/v1/tools").raise_for_status()
    response = llamastack_client.post(
        "/v1/tool-runtime/invoke",
        json={"tool_name": "get_incident", "kwargs": {"ticket_number": mock_incident["number"]}},
    )
    response.raise_for_status()
    result = response.json()
    assert not result.get("error_message"), result
    content = result["content"]
    incident = json.loads(content) if isinstance(content, str) else json.loads(content[0]["text"])
    assert incident["ticket_number"] == mock_incident["number"]
    assert incident["short_description"] == mock_incident["short_description"]
