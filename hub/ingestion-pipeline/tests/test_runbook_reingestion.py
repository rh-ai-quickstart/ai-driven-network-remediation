import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from ingestion_pipeline.clients.llamastack import DuplicateVectorStoreNameError, LlamaStackVectorStoreClient
from ingestion_pipeline.service import EmptyRunbookCorpusError, IngestionPipelineService


class Page:
    def __init__(self, data, next_page=None):
        self.data = data
        self._next_page = next_page

    def has_next_page(self):
        return self._next_page is not None

    def get_next_page(self):
        return self._next_page


def _client(stores_page):
    client = object.__new__(LlamaStackVectorStoreClient)
    client._vector_store_name = "noc_runbooks"
    client._client = SimpleNamespace(
        vector_stores=SimpleNamespace(
            list=Mock(return_value=stores_page),
            create=Mock(),
            files=SimpleNamespace(delete=Mock()),
        )
    )
    return client


def _store(id, name="noc_runbooks"):
    return SimpleNamespace(id=id, name=name, status="completed", file_counts=SimpleNamespace(model_dump=dict))


def test_store_lookup_scans_all_pages_for_exact_name():
    client = _client(Page([_store("wrong", "noc_runbooks_backup")], Page([_store("target")])))

    assert client.ensure_vector_store().id == "target"
    client._client.vector_stores.create.assert_not_called()


def test_duplicate_store_names_fail_before_any_attachment_is_removed():
    client = _client(Page([_store("first")], Page([_store("second")])))

    with pytest.raises(DuplicateVectorStoreNameError, match="Multiple vector stores named 'noc_runbooks'"):
        client.ensure_vector_store()
    client._client.vector_stores.files.delete.assert_not_called()


def test_vector_store_summary_reports_duplicate_name(monkeypatch):
    service = IngestionPipelineService()
    service._vector_client = _client(Page([_store("first")], Page([_store("second")])))
    app_module = importlib.import_module("ingestion_pipeline.app")
    monkeypatch.setattr(app_module, "service", service)
    api = TestClient(app_module.app, raise_server_exceptions=False)

    response = api.get("/vector-store")

    assert response.status_code == 409
    assert response.json()["detail"] == "Multiple vector stores named 'noc_runbooks'"


def test_empty_source_preserves_existing_attachments():
    service = IngestionPipelineService()
    service._minio_client = SimpleNamespace(load_prefix_text_objects=Mock(return_value=[]))
    service._vector_client = SimpleNamespace(ensure_vector_store=Mock(), replace_text_batch=Mock())

    with pytest.raises(EmptyRunbookCorpusError, match="No runbooks found"):
        service.ingest()

    service._vector_client.ensure_vector_store.assert_not_called()
    service._vector_client.replace_text_batch.assert_not_called()
    assert service._ingest_lock.locked() is False


class AttachmentBackend:
    def __init__(self):
        self.attachments = ["old-a", "old-b"]
        self.uploaded_contents = {}
        self.chunk_owners = {}
        self.deleted = []
        self.fail_once = False
        self.retain_on_delete = False
        self.add_during_upload = False
        self.list_error = False
        self._next_file = 0

    def list(self, vector_store_id, limit):
        assert vector_store_id == "store-1"
        assert limit == 100
        if self.list_error:
            raise RuntimeError("unreadable attachment metadata")
        ids = list(self.attachments)
        return Page([SimpleNamespace(id=ids[0])], Page([SimpleNamespace(id=id) for id in ids[1:]])) if ids else Page([])

    def delete(self, file_id, *, vector_store_id):
        assert vector_store_id == "store-1"
        self.deleted.append(file_id)
        if self.fail_once and file_id == "old-b":
            self.fail_once = False
            return SimpleNamespace(deleted=False)
        if not self.retain_on_delete:
            self.attachments.remove(file_id)
            if content := self.uploaded_contents.get(file_id):
                self.chunk_owners.pop(content, None)
        return SimpleNamespace(deleted=True)

    def upload(self, *, file, purpose):
        assert purpose == "assistants"
        if self.add_during_upload:
            self.attachments.append("concurrent-playbook")
        self._next_file += 1
        file_id = f"new-{self._next_file}"
        self.uploaded_contents[file_id] = file[1]
        return SimpleNamespace(id=file_id)

    def attach(self, vector_store_id, *, file_id, attributes, chunking_strategy):
        assert vector_store_id == "store-1"
        self.attachments.append(file_id)
        # Model one content-keyed chunk per file, as in OGX 0.6.1.
        self.chunk_owners[self.uploaded_contents[file_id]] = file_id
        return SimpleNamespace(id=file_id, vector_store_id=vector_store_id, status="completed", attributes=attributes)


@pytest.fixture
def runbook_api(monkeypatch):
    backend = AttachmentBackend()
    vector_client = _client(Page([_store("store-1")]))
    vector_client._chunk_size_tokens = 800
    vector_client._chunk_overlap_tokens = 80
    vector_client._client.files = SimpleNamespace(create=backend.upload)
    vector_client._client.vector_stores.files = SimpleNamespace(
        list=backend.list, delete=backend.delete, create=backend.attach
    )
    service = IngestionPipelineService()
    service._minio_client = SimpleNamespace(
        load_prefix_text_objects=Mock(
            return_value=[SimpleNamespace(object_name="runbooks/example.md", content="example")]
        )
    )
    service._vector_client = vector_client
    app_module = importlib.import_module("ingestion_pipeline.app")
    monkeypatch.setattr(app_module, "_get_service", lambda: service)
    return TestClient(app_module.app, raise_server_exceptions=False), backend


def test_reingestion_keeps_identical_replacement_searchable(runbook_api):
    api, backend = runbook_api
    first = api.post("/runbooks/ingest")
    assert first.status_code == 200
    original_id = first.json()["objects"][0]["id"]
    assert backend.chunk_owners == {b"example": original_id}
    backend.attachments.append("generated-playbook")

    response = api.post("/runbooks/ingest")

    assert response.status_code == 200
    replacement = response.json()["objects"][0]
    assert replacement["id"] != original_id
    assert replacement["vector_store_id"] == "store-1"
    assert backend.attachments == [replacement["id"]]
    assert backend.chunk_owners == {b"example": replacement["id"]}


def test_failed_deletion_stops_before_upload_and_rerun_removes_old_files(runbook_api):
    api, backend = runbook_api
    backend.fail_once = True

    failed = api.post("/runbooks/ingest")

    assert failed.status_code == 502
    assert failed.json()["detail"] == (
        "Failed to detach file 'old-b' from vector store 'store-1': provider did not confirm deletion"
    )
    assert backend.deleted == ["old-a", "old-b"]
    assert backend.attachments == ["old-b"]
    assert not backend.uploaded_contents

    succeeded = api.post("/runbooks/ingest")

    assert succeeded.status_code == 200
    assert succeeded.json()["objects"][0]["vector_store_id"] == "store-1"
    assert backend.attachments == ["new-1"]


def test_reingestion_fails_if_original_attachments_remain(runbook_api):
    api, backend = runbook_api
    backend.retain_on_delete = True

    response = api.post("/runbooks/ingest")

    assert response.status_code == 502
    assert response.json()["detail"] == "Previous attachments remain in vector store 'store-1': ['old-a', 'old-b']"
    assert backend.attachments == ["old-a", "old-b"]
    assert not backend.uploaded_contents


def test_playbook_added_during_upload_stays_attached(runbook_api):
    api, backend = runbook_api
    backend.add_during_upload = True

    response = api.post("/runbooks/ingest")

    assert response.status_code == 200
    assert backend.attachments == ["concurrent-playbook", "new-1"]


def test_attachment_list_error_fails_clearly_without_recreating_store(runbook_api):
    api, backend = runbook_api
    backend.list_error = True

    response = api.post("/runbooks/ingest")

    assert response.status_code == 502
    assert "Could not list attachments in vector store 'store-1'" in response.json()["detail"]
    assert "unreadable attachment metadata" in response.json()["detail"]
    assert backend.attachments == ["old-a", "old-b"]
    assert backend.deleted == []
    assert not backend.uploaded_contents
