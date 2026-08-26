import pytest

pytestmark = pytest.mark.telco

# The packaged vendor PDFs are large (500+ pages combined), and each page/section becomes its
# own vector-store file, so a full ingest run involves hundreds of embedding calls.
_INGEST_TIMEOUT_SECONDS = 1200.0


def _sync_telco_docs(ingestion_client) -> dict:
    response = ingestion_client.post("/telco-docs/sync", timeout=_INGEST_TIMEOUT_SECONDS)
    assert response.status_code == 200
    return response.json()


def test_telco_docs_sync_ingest_and_content_flow(ingestion_client):
    sync_data = _sync_telco_docs(ingestion_client)
    assert sync_data["bucket"]
    assert sync_data["prefix"] == "telco-docs/"
    assert sync_data["converted_count"] > 0
    assert any(name.endswith(".md") for name in sync_data["converted_objects"])

    ingest_response = ingestion_client.post("/telco-docs/ingest", timeout=_INGEST_TIMEOUT_SECONDS)
    assert ingest_response.status_code == 200
    ingest_data = ingest_response.json()
    assert ingest_data["prefix"] == "telco-docs/"
    assert ingest_data["ingested_count"] > 0
    assert ingest_data["objects"][0]["id"]
    assert ingest_data["objects"][0]["vector_store_id"]
    assert ingest_data["objects"][0]["attributes"]["source_type"] == "vendor_doc"
    assert ingest_data["objects"][0]["attributes"]["source_name"].startswith("telco-docs/")

    response = ingestion_client.get(
        f"/vector-store/files/{ingest_data['objects'][0]['id']}/content",
        params={"vector_store_id": ingest_data["objects"][0]["vector_store_id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == ingest_data["objects"][0]["id"]
    assert data["data"]
    assert data["data"][0]["text"]
    assert "metadata" in data["data"][0]
    assert "embedding" in data["data"][0]
