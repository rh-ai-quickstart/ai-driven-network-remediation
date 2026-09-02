import os

import httpx
import pytest


@pytest.fixture(scope="session")
def ran_ml_service_client():
    base_url = os.environ.get("RAN_ML_SERVICE_URL", "http://localhost:8080")
    try:
        httpx.get(f"{base_url}/health", timeout=5.0)
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip(f"ran-ml-service not reachable at {base_url} (requires model weights + port-forward)")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
