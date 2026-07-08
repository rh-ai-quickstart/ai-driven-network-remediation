def test_openshift_health(mcp_openshift_client):
    response = mcp_openshift_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_lokistack_health(mcp_lokistack_client):
    response = mcp_lokistack_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_kafka_health(mcp_kafka_client):
    response = mcp_kafka_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_aap_health(mcp_aap_client):
    response = mcp_aap_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_servicenow_health(mcp_servicenow_client):
    response = mcp_servicenow_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}
