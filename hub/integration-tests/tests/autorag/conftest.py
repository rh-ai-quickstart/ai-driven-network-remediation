import json
import os
import time
from pathlib import Path

import httpx
import pytest
from common_helpers import sync_runbooks
from helpers import discover_sentence_transformers_model

_AUTORAG_URL = os.environ.get("AUTORAG_URL", "http://localhost:8322")
_INGESTION_URL = os.environ.get("INGESTION_PIPELINE_URL", "http://localhost:8000")
_E2E_CASE_LIMIT = int(os.environ.get("AUTORAG_E2E_CASE_LIMIT", "8"))
_SERVICE_READY_TIMEOUT = int(os.environ.get("SERVICE_READY_TIMEOUT", "90"))
_TEST_DATA_PATH = Path(__file__).resolve().parents[3] / "autorag" / "test-data.json"


def _wait_for_health(base_url: str, path: str, name: str) -> None:
    deadline = time.monotonic() + _SERVICE_READY_TIMEOUT
    backoff = 1
    last_err: str | None = None

    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}{path}", timeout=5)
            if response.status_code == 200:
                return
            last_err = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_err = str(exc)

        time.sleep(backoff)
        backoff = min(backoff * 2, 8)

    pytest.fail(f"{name} ({base_url}{path}) not healthy after {_SERVICE_READY_TIMEOUT}s: {last_err}")


@pytest.fixture(scope="session")
def ingestion_client():
    _wait_for_health(_INGESTION_URL, "/health", "ingestion-pipeline")
    with httpx.Client(base_url=_INGESTION_URL, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def autorag_client():
    _wait_for_health(_AUTORAG_URL, "/v1/health", "autorag")
    with httpx.Client(base_url=_AUTORAG_URL, timeout=60.0) as client:
        yield client


@pytest.fixture(scope="session")
def embedding_model_id(autorag_client):
    return discover_sentence_transformers_model(autorag_client)


@pytest.fixture(scope="session")
def ingested_vector_store(ingestion_client):
    sync_runbooks(ingestion_client)
    ingest_response = ingestion_client.post("/runbooks/ingest", timeout=120.0)
    assert ingest_response.status_code == 200

    store_response = ingestion_client.get("/vector-store")
    assert store_response.status_code == 200
    return store_response.json()["id"]


@pytest.fixture(scope="session")
def rag_e2e_cases():
    cases = json.loads(_TEST_DATA_PATH.read_text(encoding="utf-8"))
    return cases[:_E2E_CASE_LIMIT]
