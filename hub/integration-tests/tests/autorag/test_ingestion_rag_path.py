import os

import pytest

from common_helpers import sync_runbooks
from helpers import is_sentence_transformers_model


@pytest.mark.integration
def test_ingestion_models_list_sentence_transformers(ingestion_client):
    response = ingestion_client.get("/models")
    assert response.status_code == 200
    models = response.json()["models"]
    assert any(is_sentence_transformers_model(model) for model in models)


@pytest.mark.integration
def test_sync_ingest_populates_noc_runbooks(ingestion_client):
    sync_runbooks(ingestion_client)
    ingest_response = ingestion_client.post("/runbooks/ingest", timeout=120.0)
    assert ingest_response.status_code == 200

    store_response = ingestion_client.get("/vector-store")
    assert store_response.status_code == 200
    data = store_response.json()
    assert data["name"] == os.environ.get("EXPECTED_VECTOR_STORE_NAME", "noc_runbooks")
    assert data["file_counts"]["total"] > 0


@pytest.mark.integration
def test_ingested_file_content_has_embeddings(ingestion_client):
    sync_runbooks(ingestion_client)
    ingest_response = ingestion_client.post("/runbooks/ingest", timeout=120.0)
    assert ingest_response.status_code == 200
    ingest_data = ingest_response.json()
    file_id = ingest_data["objects"][0]["id"]

    response = ingestion_client.get(
        f"/vector-store/files/{file_id}/content",
        timeout=30.0,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["data"]
    assert data["data"][0]["text"]
    assert "metadata" in data["data"][0]
    assert data["data"][0]["embedding"]
