import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from ingestion_pipeline.clients.llamastack import (
    AttachmentListError,
    DuplicateVectorStoreNameError,
    FailedIngestItem,
    IngestBatchResult,
)
from ingestion_pipeline.service import IngestionPipelineService


@pytest.fixture
def telco_api(monkeypatch):
    service = IngestionPipelineService()
    service._minio_client = SimpleNamespace(
        load_prefix_text_objects=Mock(
            return_value=[SimpleNamespace(object_name="telco-docs/vendor.pdf.md", content="# Section\n\nContent")]
        )
    )
    vector_client = SimpleNamespace(
        ensure_vector_store=Mock(return_value=SimpleNamespace(id="stable-store")),
        clear_vector_store_files=Mock(),
        ingest_text_batch=Mock(
            return_value=IngestBatchResult(
                succeeded=[
                    SimpleNamespace(
                        id="new-file",
                        vector_store_id="stable-store",
                        status="completed",
                        attributes={
                            "source_type": "vendor_doc",
                            "source_name": "telco-docs/vendor.pdf",
                            "section": "Section",
                        },
                    )
                ],
                failed=[],
            )
        ),
    )
    service._telco_vector_client = vector_client
    app_module = importlib.import_module("ingestion_pipeline.app")
    monkeypatch.setattr(app_module, "_get_service", lambda: service)
    return TestClient(app_module.app, raise_server_exceptions=False), service, vector_client


@pytest.mark.parametrize("content", ["", "  \n  ", "# Heading only\n"])
def test_zero_units_preserve_existing_store(telco_api, content):
    api, service, vector_client = telco_api
    service._minio_client.load_prefix_text_objects.return_value[0].content = content

    response = api.post("/telco-docs/ingest")

    assert response.status_code == 400
    assert "existing attachments were preserved" in response.json()["detail"]
    vector_client.ensure_vector_store.assert_not_called()
    vector_client.clear_vector_store_files.assert_not_called()
    assert service._telco_ingest_lock.locked() is False


def test_split_failure_preserves_existing_store(telco_api, monkeypatch):
    api, _, vector_client = telco_api
    service_module = importlib.import_module("ingestion_pipeline.service")
    monkeypatch.setattr(service_module, "split_markdown_units", Mock(side_effect=ValueError("bad document")))

    assert api.post("/telco-docs/ingest").status_code >= 400
    vector_client.ensure_vector_store.assert_not_called()
    vector_client.clear_vector_store_files.assert_not_called()


def test_duplicate_name_fails_before_clear(telco_api):
    api, _, vector_client = telco_api
    vector_client.ensure_vector_store.side_effect = DuplicateVectorStoreNameError(
        "Multiple vector stores named 'telco_oran_docs'"
    )

    response = api.post("/telco-docs/ingest")

    assert response.status_code == 409
    assert "Multiple vector stores" in response.json()["detail"]
    vector_client.clear_vector_store_files.assert_not_called()


def test_clear_happens_after_units_are_prepared_and_keeps_response_shape(telco_api):
    api, _, vector_client = telco_api

    response = api.post("/telco-docs/ingest")

    assert response.status_code == 200
    assert response.json() == {
        "bucket": response.json()["bucket"],
        "prefix": response.json()["prefix"],
        "ingested_count": 1,
        "failed_count": 0,
        "objects": [
            {
                "id": "new-file",
                "vector_store_id": "stable-store",
                "status": "completed",
                "attributes": {
                    "source_type": "vendor_doc",
                    "source_name": "telco-docs/vendor.pdf",
                    "section": "Section",
                },
            }
        ],
        "failed": [],
    }
    items = vector_client.ingest_text_batch.call_args.kwargs["items"]
    assert [(item.filename, item.content) for item in items] == [("vendor#001.md", "Content")]
    assert vector_client.clear_vector_store_files.call_args.args == ("stable-store",)
    assert vector_client.ensure_vector_store.call_count == 1


def test_partial_clear_can_be_retried(telco_api):
    api, _, vector_client = telco_api
    vector_client.clear_vector_store_files.side_effect = [RuntimeError("partial clear"), None]

    assert api.post("/telco-docs/ingest").status_code >= 400
    vector_client.ingest_text_batch.assert_not_called()
    assert api.post("/telco-docs/ingest").status_code == 200
    assert vector_client.clear_vector_store_files.call_count == 2


def test_attachment_list_error_is_reported_without_upload(telco_api):
    api, _, vector_client = telco_api
    vector_client.clear_vector_store_files.side_effect = AttachmentListError(
        "Could not list attachments in vector store 'stable-store': unreadable attachment metadata"
    )

    response = api.post("/telco-docs/ingest")

    assert response.status_code == 502
    assert "unreadable attachment metadata" in response.json()["detail"]
    vector_client.ingest_text_batch.assert_not_called()


def test_partial_upload_can_be_retried(telco_api):
    api, _, vector_client = telco_api
    prepared_item = SimpleNamespace(filename="vendor#001.md")
    success = vector_client.ingest_text_batch.return_value
    vector_client.ingest_text_batch.side_effect = [
        IngestBatchResult(succeeded=[], failed=[FailedIngestItem(item=prepared_item, reason="upload failed")]),
        success,
    ]

    partial = api.post("/telco-docs/ingest")
    assert partial.status_code == 502
    assert partial.json()["detail"]["failed"] == [{"object_name": "vendor#001.md", "reason": "upload failed"}]
    assert api.post("/telco-docs/ingest").json()["ingested_count"] == 1
    assert vector_client.clear_vector_store_files.call_count == 2
    assert all(call.args == ("stable-store",) for call in vector_client.clear_vector_store_files.call_args_list)


def test_partial_source_sync_still_allows_prepared_units(telco_api, monkeypatch, tmp_path):
    api, service, _ = telco_api
    mandatory = tmp_path / "mandatory"
    mandatory.mkdir()
    (mandatory / "good.md").write_text("# Good\n\nContent", encoding="utf-8")
    (mandatory / "bad.md").write_bytes(b"\xff")
    service_module = importlib.import_module("ingestion_pipeline.service")
    monkeypatch.setattr(
        service_module,
        "settings",
        SimpleNamespace(
            telco_docs_dir=tmp_path,
            telco_docs_include_optional=False,
            minio_telco_docs_prefix="telco-docs/",
            minio_bucket="docs",
            telco_vector_store_name="telco_oran_docs",
        ),
    )
    service._minio_client.ensure_bucket = Mock()
    service._minio_client.delete_prefix_objects = Mock()
    service._minio_client.put_text_object = Mock()
    service._minio_client.load_prefix_text_objects.return_value = [
        SimpleNamespace(object_name="telco-docs/good.md.md", content="# Good\n\nContent")
    ]

    sync = api.post("/telco-docs/sync")
    ingest = api.post("/telco-docs/ingest")

    assert sync.status_code == 200
    assert sync.json()["converted_count"] == 1
    assert sync.json()["failed_count"] == 1
    assert ingest.status_code == 200
    assert ingest.json()["ingested_count"] == 1
