def test_models_list_not_empty(ingestion_client):
    response = ingestion_client.get("/models")
    assert response.status_code == 200
    data = response.json()
    assert len(data["models"]) > 0
