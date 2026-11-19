import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from ingestion_pipeline.clients.llamastack import LlamaStackVectorStoreClient
from ingestion_pipeline.service import IngestionPipelineService


class AttachmentBackend:
    def __init__(self):
        self.attachments = []
        self.uploaded_names = {}
        self.next_id = 0
        self.failure = None
        self.detach_errors = {}
        self.retain_on_delete = set()
        self.unlisted_files = set()
        self.pending_name = None
        self.pending_file = None
        self.searchable_before_ready = set()

    def list(self, vector_store_id, limit):
        return page([SimpleNamespace(id=file_id) for file_id in self.attachments])

    def delete(self, file_id, *, vector_store_id):
        if file_id in self.detach_errors:
            raise RuntimeError(self.detach_errors[file_id])
        if file_id not in self.retain_on_delete:
            self.attachments.remove(file_id)
        return SimpleNamespace(deleted=True)

    def upload(self, *, file, purpose):
        self.next_id += 1
        file_id = f"file-{self.next_id}"
        self.uploaded_names[file_id] = file[0]
        return SimpleNamespace(id=file_id)

    def attach(self, vector_store_id, *, file_id, attributes, chunking_strategy):
        name = self.uploaded_names[file_id]
        if self.failure == (name, "api_error"):
            raise RuntimeError("provider rejected attachment")
        if self.failure == (name, "lost_metadata"):
            self.unlisted_files.add(file_id)
            raise RuntimeError("provider could not persist attachment metadata")
        self.attachments.append(file_id)
        if self.failure == (name, "lost_response"):
            raise RuntimeError("attachment response lost")
        if self.failure == (name, "failed_status"):
            return SimpleNamespace(
                id=file_id,
                vector_store_id=vector_store_id,
                status="failed",
                attributes=attributes,
                last_error=SimpleNamespace(message="embedding failed"),
            )
        if self.failure == (name, "failed_status_no_error"):
            return SimpleNamespace(id=file_id, vector_store_id=vector_store_id, status="failed", attributes=attributes)
        if self.failure == (name, "cancelled_status"):
            return SimpleNamespace(
                id=file_id, vector_store_id=vector_store_id, status="cancelled", attributes=attributes
            )
        if self.pending_name == name:
            self.pending_file = SimpleNamespace(
                id=file_id, vector_store_id=vector_store_id, status="in_progress", attributes=attributes
            )
            return self.pending_file
        return SimpleNamespace(id=file_id, vector_store_id=vector_store_id, status="completed", attributes=attributes)

    def retrieve(self, file_id, *, vector_store_id):
        assert (file_id, vector_store_id) == (self.pending_file.id, self.pending_file.vector_store_id)
        self.searchable_before_ready = set(self.attachments) - {file_id}
        self.pending_file.status = "completed"
        return self.pending_file


def page(items):
    return SimpleNamespace(data=items, has_next_page=lambda: False)


@pytest.fixture(params=["runbooks", "telco-docs"])
def ingestion_api(request, monkeypatch):
    corpus = request.param
    backend = AttachmentBackend()
    vector_client = object.__new__(LlamaStackVectorStoreClient)
    vector_client._vector_store_name = "test-store"
    vector_client._chunk_size_tokens = 800
    vector_client._chunk_overlap_tokens = 80
    vector_client._client = SimpleNamespace(
        files=SimpleNamespace(create=backend.upload),
        vector_stores=SimpleNamespace(
            list=lambda limit: page(
                [
                    SimpleNamespace(
                        id="stable-store",
                        name="test-store",
                        status="completed",
                        file_counts=SimpleNamespace(model_dump=dict),
                    )
                ]
            ),
            files=SimpleNamespace(
                list=backend.list, delete=backend.delete, create=backend.attach, retrieve=backend.retrieve
            ),
        ),
    )
    service = IngestionPipelineService()
    if corpus == "runbooks":
        objects = [
            SimpleNamespace(object_name="runbooks/first.md", content="first"),
            SimpleNamespace(object_name="runbooks/second.md", content="second"),
        ]
        failed_name = "second.md"
        service._vector_client = vector_client
    else:
        objects = [
            SimpleNamespace(object_name="telco-docs/first.pdf.md", content="# First\n\nfirst"),
            SimpleNamespace(object_name="telco-docs/second.pdf.md", content="# Second\n\nsecond"),
        ]
        failed_name = "second#001.md"
        service._telco_vector_client = vector_client
    service._minio_client = SimpleNamespace(load_prefix_text_objects=Mock(return_value=objects))
    app_module = importlib.import_module("ingestion_pipeline.app")
    monkeypatch.setattr(app_module, "_get_service", lambda: service)
    return TestClient(app_module.app, raise_server_exceptions=False), backend, corpus, failed_name


def test_ingest_waits_for_replacement_attachments_to_complete(ingestion_api):
    api, backend, corpus, last_name = ingestion_api
    backend.attachments = ["old-doc", "old-generated-playbook"]
    backend.pending_name = last_name

    response = api.post(f"/{corpus}/ingest")

    assert response.status_code == 200
    assert backend.searchable_before_ready == {"file-1"}
    assert [item["status"] for item in response.json()["objects"]] == ["completed", "completed"]
    assert backend.attachments == ["file-1", "file-2"]


def test_provider_detach_failure_reports_reason(ingestion_api):
    api, backend, corpus, _ = ingestion_api
    backend.attachments = ["old-doc"]
    backend.detach_errors["old-doc"] = "provider rejected deletion"

    response = api.post(f"/{corpus}/ingest")

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Failed to detach file 'old-doc' from vector store 'stable-store': provider rejected deletion"
    )
    assert not backend.uploaded_names


@pytest.mark.parametrize(
    "failure_kind,reason",
    [
        ("api_error", "provider rejected attachment"),
        ("lost_response", "attachment response lost"),
        ("failed_status", "embedding failed"),
        ("failed_status_no_error", "Attachment status: failed"),
        ("cancelled_status", "Attachment status: cancelled"),
    ],
)
def test_failed_attachment_rolls_back_replacement_and_allows_retry(ingestion_api, failure_kind, reason):
    api, backend, corpus, failed_name = ingestion_api
    backend.attachments = ["old-doc", "old-generated-playbook"]
    backend.failure = (failed_name, failure_kind)

    failed = api.post(f"/{corpus}/ingest")

    assert failed.status_code == 502
    assert failed.json()["detail"]["failed"] == [{"object_name": failed_name, "reason": reason}]
    assert failed.json()["detail"]["failed_count"] == 1
    assert failed.json()["detail"]["ingested_count"] == 1
    assert not backend.attachments

    backend.failure = None
    succeeded = api.post(f"/{corpus}/ingest")

    assert succeeded.status_code == 200
    assert succeeded.json()["ingested_count"] == 2
    assert succeeded.json()["failed_count"] == 0
    assert succeeded.json()["failed"] == []
    assert [item["id"] for item in succeeded.json()["objects"]] == backend.attachments
    assert all(item["vector_store_id"] == "stable-store" for item in succeeded.json()["objects"])
    assert all(item["status"] == "completed" for item in succeeded.json()["objects"])
    assert all(item["attributes"] for item in succeeded.json()["objects"])
    assert [backend.uploaded_names[file_id] for file_id in backend.attachments] == (
        ["first.md", "second.md"] if corpus == "runbooks" else ["first#001.md", "second#001.md"]
    )


def test_failed_rollback_reports_the_leftover_and_cleans_up_other_attachments(ingestion_api, caplog):
    api, backend, corpus, failed_name = ingestion_api
    backend.attachments = ["old-doc"]
    backend.failure = (failed_name, "failed_status")
    backend.retain_on_delete.add("file-1")

    response = api.post(f"/{corpus}/ingest")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["failed"] == [{"object_name": failed_name, "reason": "embedding failed"}]
    assert detail["rollback_errors"] == ["Rollback in vector store 'stable-store' could not remove ['file-1']"]
    assert backend.attachments == ["file-1"]
    assert any("Rollback" in record.message and "file-1" in record.message for record in caplog.records)


def test_rollback_reports_a_failed_detach_even_when_the_file_is_unlisted(ingestion_api, caplog):
    api, backend, corpus, failed_name = ingestion_api
    backend.attachments = ["old-doc"]
    backend.failure = (failed_name, "lost_metadata")
    backend.detach_errors["file-2"] = "attachment metadata not found"

    response = api.post(f"/{corpus}/ingest")

    assert response.status_code == 502
    assert response.json()["detail"]["rollback_errors"] == [
        (
            "Rollback could not confirm detach: Failed to detach file 'file-2' from vector store 'stable-store': "
            "attachment metadata not found"
        )
    ]
    assert not backend.attachments
    assert backend.unlisted_files == {"file-2"}
    assert any("Rollback" in record.message and "file-2" in record.message for record in caplog.records)
