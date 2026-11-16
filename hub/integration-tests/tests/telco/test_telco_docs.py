import os
import time
from uuid import uuid4

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


def _attachments(llamastack_client, store_id):
    attachments = []
    after = None
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        response = llamastack_client.get(f"/v1/vector_stores/{store_id}/files", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        attachments.extend(page["data"])
        if not page.get("has_more"):
            return attachments
        after = page["last_id"]


def _search(llamastack_client, store_id, query):
    response = llamastack_client.post(f"/v1/vector_stores/{store_id}/search", json={"query": query})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _search_contains(llamastack_client, store_id, query, text):
    return any(
        text in part.get("text", "")
        for result in _search(llamastack_client, store_id, query)
        for part in result.get("content", [])
    )


def test_telco_docs_sync_ingest_and_content_flow(ingestion_client, llamastack_client, rag_reader):
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

    store_id = ingest_data["objects"][0]["vector_store_id"]
    store_response = llamastack_client.get(f"/v1/vector_stores/{store_id}")
    assert store_response.status_code == 200, store_response.text
    assert store_response.json()["name"] == os.environ.get("EXPECTED_TELCO_VECTOR_STORE_NAME", "telco_oran_docs")
    store_name = store_response.json()["name"]
    first_attachments = _attachments(llamastack_client, store_id)
    first_ids = {item["id"] for item in first_attachments}
    assert len(first_attachments) == _EXPECTED_INGESTED_FILES

    marker = f"obsolete-vendor-{uuid4().hex}"
    uploaded = llamastack_client.post(
        "/v1/files",
        data={"purpose": "assistants"},
        files={"file": ("obsolete-vendor.md", f"# {marker}\n{marker}".encode(), "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    old_file_id = uploaded.json()["id"]
    attached = llamastack_client.post(
        f"/v1/vector_stores/{store_id}/files",
        json={"file_id": old_file_id, "attributes": {"source_type": "vendor_doc"}},
    )
    assert attached.status_code == 200, attached.text
    deadline = time.monotonic() + 120
    while not _search_contains(llamastack_client, store_id, marker, marker):
        assert time.monotonic() < deadline, "Obsolete vendor content never became searchable"
        time.sleep(2)
    assert any(marker in snippet for snippet in rag_reader(store_name, marker))
    assert len(_attachments(llamastack_client, store_id)) == _EXPECTED_INGESTED_FILES + 1

    # Each replacement must clear every attachment page and keep the original store ID.
    previous_ids = first_ids | {old_file_id}
    for _ in range(2):
        response = ingestion_client.post("/telco-docs/ingest", timeout=_INGEST_TIMEOUT_SECONDS)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["failed_count"] == 0, data["failed"]
        assert data["ingested_count"] == _EXPECTED_INGESTED_FILES
        assert len(data["objects"]) == _EXPECTED_INGESTED_FILES
        assert {item["vector_store_id"] for item in data["objects"]} == {store_id}
        assert {item["attributes"]["source_name"] for item in data["objects"]} == _EXPECTED_SOURCES

        attachments = _attachments(llamastack_client, store_id)
        attachment_ids = {item["id"] for item in attachments}
        assert len(attachments) == _EXPECTED_INGESTED_FILES
        assert not (attachment_ids & previous_ids)
        assert attachment_ids == {item["id"] for item in data["objects"]}
        deadline = time.monotonic() + 120
        while _search_contains(llamastack_client, store_id, marker, marker):
            assert time.monotonic() < deadline, "Obsolete vendor content remained searchable"
            time.sleep(2)
        while not _search(llamastack_client, store_id, "gNodeB"):
            assert time.monotonic() < deadline, "Replacement vendor documents never became searchable"
            time.sleep(2)
        snippets = rag_reader(store_name, "gNodeB")
        assert snippets
        previous_ids = attachment_ids

    final_store = llamastack_client.get(f"/v1/vector_stores/{store_id}")
    assert final_store.status_code == 200, final_store.text
    assert final_store.json()["file_counts"]["total"] == _EXPECTED_INGESTED_FILES

    # verify content retrieval
    first_obj = data["objects"][0]
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
