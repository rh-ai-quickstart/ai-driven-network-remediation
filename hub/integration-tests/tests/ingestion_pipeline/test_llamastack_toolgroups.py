def test_llamastack_registers_openshift_mcp_toolgroup(llamastack_client):
    response = llamastack_client.get("/v1/toolgroups")
    assert response.status_code == 200

    data = response.json()
    toolgroups = data.get("data", data)

    openshift_toolgroup = next(
        (
            item
            for item in toolgroups
            if item.get("identifier") == "mcp::noc-openshift"
            or item.get("provider_resource_id") == "mcp::noc-openshift"
        ),
        None,
    )

    assert openshift_toolgroup is not None
    assert openshift_toolgroup["provider_id"] == "model-context-protocol"
    assert openshift_toolgroup["mcp_endpoint"]["uri"].endswith("/mcp")
    assert "mcp-noc-openshift" in openshift_toolgroup["mcp_endpoint"]["uri"]
