def test_health(agent_service_client):
    response = agent_service_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
