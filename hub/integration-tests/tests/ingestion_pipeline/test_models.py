def sync_runbooks(ingestion_client):
    response = ingestion_client.post("/runbooks/sync", timeout=30.0)
    assert response.status_code == 200
    return response.json()


def test_models_list_not_empty(ingestion_client):
    response = ingestion_client.get("/models")
    assert response.status_code == 200
    data = response.json()
    assert len(data["models"]) > 0


def test_vector_store_endpoint_returns_summary(ingestion_client):
    response = ingestion_client.get("/vector-store")
    assert response.status_code == 200
    data = response.json()
    assert data["id"]
    assert "file_counts" in data


def test_runbooks_sync_ingest_and_content_flow(ingestion_client):
    sync_data = sync_runbooks(ingestion_client)
    assert sync_data["bucket"] == "runbooks"
    assert sync_data["prefix"] == "runbooks/"
    assert sync_data["uploaded_count"] + sync_data["skipped_count"] > 0
    assert "runbooks/nginx-crashloop.md" in sync_data["uploaded_objects"] or "runbooks/nginx-crashloop.md" in sync_data["skipped_objects"]

    ingest_response = ingestion_client.post("/runbooks/ingest", timeout=60.0)
    assert ingest_response.status_code == 200
    ingest_data = ingest_response.json()
    assert ingest_data["prefix"] == "runbooks/"
    assert ingest_data["ingested_count"] > 0
    assert ingest_data["objects"][0]["id"]
    assert ingest_data["objects"][0]["vector_store_id"]
    assert ingest_data["objects"][0]["attributes"]["source_type"] == "runbook"
    assert ingest_data["objects"][0]["attributes"]["source_name"].startswith("runbooks/")

    response = ingestion_client.get(
        f"/vector-store/files/{ingest_data['objects'][0]['id']}/content",
        timeout=30.0,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == ingest_data["objects"][0]["id"]
    assert data["data"]
    assert data["data"][0]["text"]
    assert "metadata" in data["data"][0]
    assert "embedding" in data["data"][0]
