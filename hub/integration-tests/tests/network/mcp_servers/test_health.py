def test_openshift_health(mcp_openshift_client):
    response = mcp_openshift_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}
