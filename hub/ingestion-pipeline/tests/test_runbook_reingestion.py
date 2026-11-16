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


def _client(stores_page, files_page=None):
    client = object.__new__(LlamaStackVectorStoreClient)
    client._vector_store_name = "noc_runbooks"
    client._client = SimpleNamespace(
        vector_stores=SimpleNamespace(
            list=Mock(return_value=stores_page),
            create=Mock(),
            files=SimpleNamespace(list=Mock(return_value=files_page), delete=Mock()),
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


def test_clear_removes_every_attachment_from_all_pages():
    files = Page([SimpleNamespace(id="generated")], Page([SimpleNamespace(id="runbook")]))
    client = _client(Page([_store("target")]), files)
    client._client.vector_stores.files.list.side_effect = [files, Page([])]

    client.clear_vector_store_files("target")

    assert [call.args[0] for call in client._client.vector_stores.files.delete.call_args_list] == [
        "generated", "runbook"
    ]


def test_failed_detach_stops_before_replacement():
    client = _client(Page([_store("target")]), Page([SimpleNamespace(id="generated")]))
    client._client.vector_stores.files.delete.return_value = SimpleNamespace(deleted=False)

    with pytest.raises(RuntimeError, match="Failed to detach file 'generated'"):
        client.clear_vector_store_files("target")


def test_empty_source_preserves_existing_attachments():
    service = object.__new__(IngestionPipelineService)
    service._ingesting = False
    service._minio_client = SimpleNamespace(load_prefix_text_objects=Mock(return_value=[]))
    service._vector_client = SimpleNamespace(ensure_vector_store=Mock(), clear_vector_store_files=Mock())

    with pytest.raises(EmptyRunbookCorpusError, match="No runbooks found"):
        service.ingest()

    service._vector_client.ensure_vector_store.assert_not_called()
    service._vector_client.clear_vector_store_files.assert_not_called()
    assert service._ingesting is False


class AttachmentBackend:
    def __init__(self):
        self.attachments = ["old-a", "old-b"]
        self.deleted = []
        self.created = []
        self.fail_once = False
        self.retain_on_delete = False
        self.add_during_clear = False
        self._next_file = 0

    def list(self, vector_store_id, limit):
        assert vector_store_id == "store-1"
        assert limit == 100
        ids = list(self.attachments)
        return Page([SimpleNamespace(id=ids[0])], Page([SimpleNamespace(id=id) for id in ids[1:]])) if ids else Page([])

    def delete(self, file_id, *, vector_store_id):
        assert vector_store_id == "store-1"
        self.deleted.append(file_id)
        if self.add_during_clear and file_id == "old-a":
            self.attachments.append("concurrent-playbook")
        if self.fail_once and file_id == "old-b":
            self.fail_once = False
            return SimpleNamespace(deleted=False)
        if not self.retain_on_delete:
            self.attachments.remove(file_id)
        return SimpleNamespace(deleted=True)

    def upload(self, *, file, purpose):
        assert purpose == "assistants"
        self._next_file += 1
        return SimpleNamespace(id=f"new-{self._next_file}")

    def attach(self, vector_store_id, *, file_id, attributes, chunking_strategy):
        assert vector_store_id == "store-1"
        self.attachments.append(file_id)
        self.created.append(file_id)
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
    service = object.__new__(IngestionPipelineService)
    service._ingesting = False
    service._minio_client = SimpleNamespace(
        load_prefix_text_objects=Mock(
            return_value=[SimpleNamespace(object_name="runbooks/example.md", content="example")]
        )
    )
    service._vector_client = vector_client
    app_module = importlib.import_module("ingestion_pipeline.app")
    monkeypatch.setattr(app_module, "_get_service", lambda: service)
    return TestClient(app_module.app, raise_server_exceptions=False), backend


def test_failed_deletion_stops_upload_and_rerun_rebuilds_corpus(runbook_api):
    api, backend = runbook_api
    backend.fail_once = True

    failed = api.post("/runbooks/ingest")

    assert failed.status_code >= 400
    assert backend.deleted == ["old-a", "old-b"]
    assert backend.created == []
    assert backend.attachments == ["old-b"]

    succeeded = api.post("/runbooks/ingest")

    assert succeeded.status_code == 200
    assert succeeded.json()["objects"][0]["vector_store_id"] == "store-1"
    assert backend.attachments == ["new-1"]


def test_clear_verifies_original_attachments_are_gone_before_upload(runbook_api):
    api, backend = runbook_api
    backend.retain_on_delete = True

    response = api.post("/runbooks/ingest")

    assert response.status_code >= 400
    assert backend.created == []
    assert backend.attachments == ["old-a", "old-b"]


def test_attachment_added_during_clear_does_not_fail_verification(runbook_api):
    api, backend = runbook_api
    backend.add_during_clear = True

    response = api.post("/runbooks/ingest")

    assert response.status_code == 200
    assert backend.attachments == ["concurrent-playbook", "new-1"]
