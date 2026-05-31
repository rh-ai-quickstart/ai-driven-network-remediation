"""Integration tests verifying noc-openshift exposes and executes its MCP tools."""

import json

EXPECTED_TOOLS = {
    "get_namespaces",
    "get_pods",
    "get_events",
    "rollout_restart",
    "patch_deployment_memory",
    "get_pod_logs",
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


def test_openshift_tools_list(mcp_openshift_client):
    """Verify the MCP tools/list endpoint returns all expected OpenShift tools."""
    response = mcp_openshift_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=MCP_HEADERS,
    )
    assert response.status_code == 200
    data = _parse_sse_json(response)
    tool_names = {t["name"] for t in data.get("result", {}).get("tools", [])}
    assert EXPECTED_TOOLS.issubset(tool_names), (
        f"Missing tools: {EXPECTED_TOOLS - tool_names}"
    )


def test_get_namespaces(mcp_openshift_client):
    """Call get_namespaces and verify dark-noc-edge is listed."""
    result = _call_tool(mcp_openshift_client, "get_namespaces")
    assert "namespaces" in result
    assert result.get("count", 0) >= 1
    names = [ns["name"] for ns in result["namespaces"]]
    assert "dark-noc-edge" in names


def test_get_pods(mcp_openshift_client):
    """Call get_pods and verify the edge-worker deployment pod is visible."""
    result = _call_tool(mcp_openshift_client, "get_pods", {"namespace": "dark-noc-edge"})
    assert "pods" in result
    assert result.get("count", 0) >= 1
    pod_names = [p["name"] for p in result["pods"]]
    assert any("edge-worker" in name for name in pod_names)


def test_get_events(mcp_openshift_client):
    """Call get_events and verify it returns a list (may be empty on a fresh namespace)."""
    result = _call_tool(mcp_openshift_client, "get_events", {"namespace": "dark-noc-edge"})
    assert "events" in result
    assert isinstance(result["events"], list)


def test_get_pod_logs(mcp_openshift_client):
    """Call get_pod_logs on the edge-worker pod."""
    pods_result = _call_tool(mcp_openshift_client, "get_pods", {"namespace": "dark-noc-edge"})
    pod_name = next(p["name"] for p in pods_result["pods"] if "edge-worker" in p["name"])

    result = _call_tool(
        mcp_openshift_client,
        "get_pod_logs",
        {"pod_name": pod_name, "namespace": "dark-noc-edge", "tail_lines": 10},
    )
    assert result["success"] is True
    assert result["pod"] == pod_name
