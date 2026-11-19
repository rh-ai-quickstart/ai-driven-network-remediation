"""HTTP behavior when operators start overlapping ingestion runs."""

import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from ingestion_pipeline.service import IngestionPipelineService


@pytest.fixture
def ingestion_api(monkeypatch):
    service = IngestionPipelineService()
    service._vector_client = _vector_client()
    service._telco_vector_client = _vector_client()
    app_module = importlib.import_module("ingestion_pipeline.app")
    monkeypatch.setattr(app_module, "_get_service", lambda: service)
    return TestClient(app_module.app, raise_server_exceptions=False), service


def _vector_client():
    return SimpleNamespace(
        ensure_vector_store=Mock(return_value=SimpleNamespace(id="store")),
        replace_text_batch=Mock(return_value=SimpleNamespace(succeeded=[], failed=[])),
    )


@pytest.mark.parametrize(
    "corpus,object_name,content",
    [
        ("runbooks", "runbooks/example.md", "example"),
        ("telco-docs", "telco-docs/example.pdf.md", "# Example\n\nContent"),
    ],
)
def test_second_request_for_same_corpus_gets_conflict(ingestion_api, corpus, object_name, content):
    api, service = ingestion_api
    entered = Event()
    release = Event()

    def load(_prefix):
        entered.set()
        assert release.wait(5)
        return [SimpleNamespace(object_name=object_name, content=content)]

    service._minio_client = SimpleNamespace(load_prefix_text_objects=load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(api.post, f"/{corpus}/ingest")
        try:
            assert entered.wait(5)
            second = pool.submit(api.post, f"/{corpus}/ingest").result(timeout=2)
            assert second.status_code == 409
            assert second.json() == {
                "detail": f"{'Runbook' if corpus == 'runbooks' else 'Telco'} ingestion is already in progress"
            }
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200


def test_runbook_and_telco_ingestion_can_run_together(ingestion_api):
    api, service = ingestion_api
    runbook_entered = Event()
    telco_entered = Event()
    release = Event()

    def load(prefix):
        if prefix.startswith("runbook"):
            runbook_entered.set()
            object_name, content = "runbooks/example.md", "example"
        else:
            telco_entered.set()
            object_name, content = "telco-docs/example.pdf.md", "# Example\n\nContent"
        assert release.wait(5)
        return [SimpleNamespace(object_name=object_name, content=content)]

    service._minio_client = SimpleNamespace(load_prefix_text_objects=load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runbooks = pool.submit(api.post, "/runbooks/ingest")
        telco = pool.submit(api.post, "/telco-docs/ingest")
        try:
            assert runbook_entered.wait(5)
            assert telco_entered.wait(5)
        finally:
            release.set()
        assert runbooks.result(timeout=5).status_code == 200
        assert telco.result(timeout=5).status_code == 200


@pytest.mark.parametrize("corpus", ["runbooks", "telco-docs"])
def test_failed_ingestion_allows_later_request(ingestion_api, corpus):
    api, service = ingestion_api
    object_name, content = (
        ("runbooks/example.md", "example")
        if corpus == "runbooks"
        else ("telco-docs/example.pdf.md", "# Example\n\nContent")
    )
    load = Mock(
        side_effect=[
            RuntimeError("storage unavailable"),
            [SimpleNamespace(object_name=object_name, content=content)],
        ]
    )
    service._minio_client = SimpleNamespace(load_prefix_text_objects=load)

    assert api.post(f"/{corpus}/ingest").status_code == 500
    retry = api.post(f"/{corpus}/ingest")
    assert retry.status_code == 200
    assert retry.json()["ingested_count"] == 0
