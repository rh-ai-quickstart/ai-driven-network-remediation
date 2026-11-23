import os
import time
from uuid import uuid4


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


def test_vector_store_uses_expected_runbook_store(ingestion_client):
    response = ingestion_client.get("/vector-store")
    assert response.status_code == 200

    data = response.json()
    expected_name = os.environ.get("EXPECTED_VECTOR_STORE_NAME", "noc_runbooks")

    assert data["id"]
    assert data["name"] == expected_name
    assert data["file_counts"]["total"] >= 0


def test_runbook_reingestion_replaces_attachments(ingestion_client, llamastack_client, rag_reader):
    """Two API ingestions keep the store ID and remove all earlier attachments."""
    assert ingestion_client.post("/runbooks/sync").status_code == 200
    store_response = ingestion_client.get("/vector-store")
    assert store_response.status_code == 200
    store_id = store_response.json()["id"]
    store_name = store_response.json()["name"]

    marker = f"obsolete-runbook-{uuid4().hex}"
    uploaded = llamastack_client.post(
        "/v1/files",
        data={"purpose": "assistants"},
        files={"file": ("generated-playbook.md", f"# {marker}\n{marker}".encode(), "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    old_file_id = uploaded.json()["id"]
    attached = llamastack_client.post(
        f"/v1/vector_stores/{store_id}/files",
        json={"file_id": old_file_id, "attributes": {"source_type": "generated_playbook"}},
    )
    assert attached.status_code == 200, attached.text

    deadline = time.monotonic() + 120
    while not _search_contains(llamastack_client, store_id, marker, marker):
        assert time.monotonic() < deadline, "Generated playbook never became searchable"
        time.sleep(2)
    assert any(marker in snippet for snippet in rag_reader(store_name, marker))

    previous_ids = {item["id"] for item in _attachments(llamastack_client, store_id)}
    expected_source_names = None
    for _ in range(2):
        response = ingestion_client.post("/runbooks/ingest", timeout=600.0)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["failed_count"] == 0, data["failed"]
        assert data["ingested_count"] > 0
        assert data["ingested_count"] == len(data["objects"])
        assert all(item["vector_store_id"] == store_id for item in data["objects"])
        assert ingestion_client.get("/vector-store").json()["id"] == store_id

        attachments = _attachments(llamastack_client, store_id)
        attachment_ids = {item["id"] for item in attachments}
        assert len(attachments) == data["ingested_count"]
        assert not (attachment_ids & previous_ids)
        source_names = {item["attributes"]["source_name"] for item in attachments}
        assert len(source_names) == len(attachments)
        if expected_source_names is None:
            expected_source_names = source_names
        else:
            assert source_names == expected_source_names
        deadline = time.monotonic() + 120
        while _search_contains(llamastack_client, store_id, marker, marker):
            assert time.monotonic() < deadline, "Old generated playbook remained searchable"
            time.sleep(2)

        results = _search(llamastack_client, store_id, "nginx crashloop")
        while not results:
            assert time.monotonic() < deadline, "Replacement runbooks never became searchable"
            time.sleep(2)
            results = _search(llamastack_client, store_id, "nginx crashloop")
        assert results
        result_content = [
            (item["file_id"], tuple(part["text"] for part in item["content"])) for item in results
        ]
        assert len(result_content) == len(set(result_content))
        assert rag_reader(store_name, "nginx crashloop")
        previous_ids = attachment_ids
