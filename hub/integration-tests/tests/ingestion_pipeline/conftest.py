import os
import time

import httpx
import pytest


_SERVICE_READY_TIMEOUT = int(os.environ.get("SERVICE_READY_TIMEOUT", "90"))


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
    base_url = os.environ.get("INGESTION_PIPELINE_URL", "http://localhost:8000")
    _wait_for_health(base_url, "/health", "ingestion-pipeline")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def llamastack_client():
    base_url = os.environ.get("LLAMASTACK_URL", "http://localhost:8321")
    _wait_for_health(base_url, "/v1/health", "llamastack")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
