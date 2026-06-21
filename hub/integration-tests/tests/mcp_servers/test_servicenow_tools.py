"""Integration tests verifying noc-servicenow exposes and executes its MCP tools."""

import json

EXPECTED_TOOLS = {
    "create_incident",
    "update_incident",
    "get_incident",
    "resolve_incident",
}

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _parse_sse_json(response) -> dict:
    """Parse a JSON-RPC result from either plain JSON or SSE response."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise ValueError(f"No data line in SSE response: {response.text}")
    return response.json()


def _call_tool(client, tool_name: str, arguments: dict | None = None) -> dict:
    """Call an MCP tool via JSON-RPC and return the parsed result content."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        },
        headers=MCP_HEADERS,
    )
    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text}"
    data = _parse_sse_json(response)
    assert "result" in data, f"No result in response: {data}"
    content = data["result"]["content"]
    assert len(content) > 0
    return json.loads(content[0]["text"])


def test_servicenow_tools_list(mcp_servicenow_client):
    """Verify the MCP tools/list endpoint returns all expected ServiceNow tools."""
    response = mcp_servicenow_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=MCP_HEADERS,
    )
    assert response.status_code == 200
    data = _parse_sse_json(response)
    tool_names = {t["name"] for t in data.get("result", {}).get("tools", [])}
    assert EXPECTED_TOOLS.issubset(tool_names), f"Missing tools: {EXPECTED_TOOLS - tool_names}"


def test_create_incident(mcp_servicenow_client):
    """Create an incident and verify ticket number is returned."""
    result = _call_tool(
        mcp_servicenow_client,
        "create_incident",
        {
            "short_description": "CI test: pod crash",
            "description": "Integration test incident",
            "priority": 3,
        },
    )
    assert result["success"] is True
    assert result["ticket_number"].startswith("INC")
    assert result["sys_id"]


def test_get_incident(mcp_servicenow_client):
    """Create then retrieve an incident."""
    created = _call_tool(
        mcp_servicenow_client,
        "create_incident",
        {
            "short_description": "CI test: get check",
            "description": "Will be retrieved",
        },
    )
    ticket = created["ticket_number"]

    result = _call_tool(mcp_servicenow_client, "get_incident", {"ticket_number": ticket})
    assert result["ticket_number"] == ticket
    assert result["short_description"] == "CI test: get check"
    assert result["state"] == "New"


def test_update_incident(mcp_servicenow_client):
    """Create, update, then verify state change."""
    created = _call_tool(
        mcp_servicenow_client,
        "create_incident",
        {
            "short_description": "CI test: update check",
            "description": "Will be updated",
        },
    )
    ticket = created["ticket_number"]

    result = _call_tool(
        mcp_servicenow_client,
        "update_incident",
        {"ticket_number": ticket, "work_notes": "Working on it", "state": "in_progress"},
    )
    assert result["success"] is True
    assert result["updated_state"] == "in_progress"


def test_resolve_incident(mcp_servicenow_client):
    """Create then resolve an incident."""
    created = _call_tool(
        mcp_servicenow_client,
        "create_incident",
        {
            "short_description": "CI test: resolve check",
            "description": "Will be resolved",
        },
    )
    ticket = created["ticket_number"]

    result = _call_tool(
        mcp_servicenow_client,
        "resolve_incident",
        {"ticket_number": ticket, "resolution_notes": "Fixed by restart"},
    )
    assert result["success"] is True
    assert result["state"] == "Resolved"
