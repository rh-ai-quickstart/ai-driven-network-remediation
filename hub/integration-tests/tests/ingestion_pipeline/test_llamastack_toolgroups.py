import os

import pytest


def _find_toolgroup(toolgroups, identifier: str):
    return next(
        (
            item
            for item in toolgroups
            if item.get("identifier") == identifier or item.get("provider_resource_id") == identifier
        ),
        None,
    )


def _assert_mcp_toolgroup(toolgroups, identifier: str, service_name: str):
    toolgroup = _find_toolgroup(toolgroups, identifier)
    assert toolgroup is not None
    assert toolgroup["provider_id"] == "model-context-protocol"
    assert toolgroup["mcp_endpoint"]["uri"].endswith("/mcp")
    assert service_name in toolgroup["mcp_endpoint"]["uri"]


@pytest.mark.parametrize(
    ("identifier", "service_name"),
    [
        ("mcp::noc-openshift", "mcp-noc-openshift"),
        ("mcp::noc-kafka", "mcp-noc-kafka"),
        ("mcp::noc-aap", "mcp-noc-aap"),
        ("mcp::noc-slack", "mcp-noc-slack"),
        ("mcp::noc-servicenow", "mcp-noc-servicenow"),
    ],
)
def test_llamastack_registers_default_mcp_toolgroups(llamastack_client, identifier: str, service_name: str):
    response = llamastack_client.get("/v1/toolgroups")
    assert response.status_code == 200

    data = response.json()
    toolgroups = data.get("data", data)

    _assert_mcp_toolgroup(toolgroups, identifier, service_name)


@pytest.mark.skipif(
    os.environ.get("ENABLE_LOKISTACK", "false").lower() != "true",
    reason="LokiStack MCP server is optional and not deployed by default",
)
def test_llamastack_registers_lokistack_mcp_toolgroup(llamastack_client):
    response = llamastack_client.get("/v1/toolgroups")
    assert response.status_code == 200

    data = response.json()
    toolgroups = data.get("data", data)

    _assert_mcp_toolgroup(toolgroups, "mcp::noc-lokistack", "mcp-noc-lokistack")
