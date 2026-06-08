"""Integration tests verifying noc-openshift exposes and executes its MCP tools."""

import os
import subprocess

from conftest import mcp_call, mcp_list_tools

EDGE_NAMESPACE = os.environ.get("EDGE_NAMESPACE", "dark-noc-edge")

EXPECTED_TOOLS = {
    "get_namespaces",
    "get_pods",
    "get_events",
    "rollout_restart",
    "patch_deployment_memory",
    "get_pod_logs",
}


def test_openshift_tools_list(mcp_openshift_client):
    """Verify the MCP tools/list endpoint returns all expected OpenShift tools."""
    tool_names = mcp_list_tools(mcp_openshift_client)
    assert EXPECTED_TOOLS.issubset(tool_names), f"Missing tools: {EXPECTED_TOOLS - tool_names}"


def test_get_namespaces(mcp_openshift_client):
    """Call get_namespaces and verify dark-noc-edge is listed."""
    result = mcp_call(mcp_openshift_client, "get_namespaces")
    assert "namespaces" in result
    assert result.get("count", 0) >= 1
    names = [ns["name"] for ns in result["namespaces"]]
    assert EDGE_NAMESPACE in names


def _oc_pod_names(namespace: str) -> set[str]:
    """Return pod names from a direct ``oc get pods`` call."""
    out = subprocess.run(
        ["oc", "get", "pods", "-n", namespace, "-o", "jsonpath={.items[*].metadata.name}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return set(out.stdout.split()) if out.stdout.strip() else set()


def test_get_pods(mcp_openshift_client):
    """Call get_pods and verify the returned set matches a direct oc query."""
    result = mcp_call(mcp_openshift_client, "get_pods", {"namespace": EDGE_NAMESPACE})
    assert "pods" in result
    mcp_names = {p["name"] for p in result["pods"]}
    oc_names = _oc_pod_names(EDGE_NAMESPACE)
    assert mcp_names == oc_names


def test_get_events(mcp_openshift_client):
    """Call get_events and verify it returns a list (may be empty on a fresh namespace)."""
    result = mcp_call(mcp_openshift_client, "get_events", {"namespace": EDGE_NAMESPACE})
    assert "events" in result
    assert isinstance(result["events"], list)


def test_get_pod_logs(mcp_openshift_client):
    """Call get_pod_logs on the first running pod in the namespace."""
    pods_result = mcp_call(mcp_openshift_client, "get_pods", {"namespace": EDGE_NAMESPACE})
    running = [p for p in pods_result["pods"] if p["status"] == "Running"]
    assert running, "No running pods found in namespace"
    pod_name = running[0]["name"]

    result = mcp_call(
        mcp_openshift_client,
        "get_pod_logs",
        {"pod_name": pod_name, "namespace": EDGE_NAMESPACE, "tail_lines": 10},
    )
    assert result["success"] is True
    assert result["pod"] == pod_name
