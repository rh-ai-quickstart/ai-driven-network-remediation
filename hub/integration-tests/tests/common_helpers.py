def sync_runbooks(ingestion_client):
    response = ingestion_client.post("/runbooks/sync", timeout=30.0)
    assert response.status_code == 200
    return response.json()
