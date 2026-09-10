import pytest

pytestmark = pytest.mark.telco

_EXPECTED_DOCS = {
    "telco-docs/ran_metrics_and_anomalies.docx.md",
    "telco-docs/gnodeb.pdf.md",
}

_EXPECTED_SOURCES = {
    "telco-docs/ran_metrics_and_anomalies.docx",
    "telco-docs/gnodeb.pdf",
}
_EXPECTED_INGESTED_FILES = 121
_SYNC_TIMEOUT_SECONDS = 300.0
_INGEST_TIMEOUT_SECONDS = 1200.0


def test_telco_docs_sync_ingest_and_content_flow(ingestion_client):
    # sync
    sync_response = ingestion_client.post("/telco-docs/sync", timeout=_SYNC_TIMEOUT_SECONDS)
    assert sync_response.status_code == 200
    sync_data = sync_response.json()
    assert sync_data["failed_count"] == 0
    assert sync_data["converted_count"] == len(sync_data["converted_objects"])
    assert set(sync_data["converted_objects"]) == _EXPECTED_DOCS

    # ingest
    ingest_response = ingestion_client.post("/telco-docs/ingest", timeout=_INGEST_TIMEOUT_SECONDS)
    assert ingest_response.status_code == 200
    ingest_data = ingest_response.json()
    assert ingest_data["failed_count"] == 0
    assert ingest_data["ingested_count"] == len(ingest_data["objects"])
    assert ingest_data["ingested_count"] == _EXPECTED_INGESTED_FILES
    ingested_sources = {obj["attributes"]["source_name"] for obj in ingest_data["objects"]}
    assert ingested_sources == _EXPECTED_SOURCES
    for obj in ingest_data["objects"]:
        assert obj["id"]
        assert obj["vector_store_id"]
        assert obj["attributes"]["source_type"] == "vendor_doc"

    # verify content retrieval
    first_obj = ingest_data["objects"][0]
    content_response = ingestion_client.get(
        f"/vector-store/files/{first_obj['id']}/content",
        params={"vector_store_id": first_obj["vector_store_id"]},
        timeout=_INGEST_TIMEOUT_SECONDS,
    )
    assert content_response.status_code == 200
    data = content_response.json()
    assert data["id"] == first_obj["id"]
    assert len(data["data"]) > 0
    for chunk in data["data"]:
        assert chunk["text"]
        assert "metadata" in chunk
        assert "embedding" in chunk
