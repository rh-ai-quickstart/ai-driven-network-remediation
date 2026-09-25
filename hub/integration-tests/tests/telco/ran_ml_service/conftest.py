import os

import httpx
import pytest


@pytest.fixture(scope="session")
def ran_ml_service_client():
    base_url = os.environ.get("RAN_ML_SERVICE_URL", "http://localhost:8009")
    try:
        response = httpx.get(f"{base_url}/health", timeout=5.0)
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip(f"ran-ml-service not reachable at {base_url} (requires model weights + port-forward)")

    # A 200 alone does not prove this is ran-ml-service — any service sharing the port
    # answers /health. Only ran-ml-service reports a "task". Fail instead of skipping:
    # a misrouted port-forward is a broken setup, not an absent service.
    try:
        task = response.json().get("task")
    except ValueError:
        task = None
    if task is None:
        pytest.fail(
            f"{base_url}/health did not answer as ran-ml-service "
            f"(HTTP {response.status_code}, body {response.text[:200]!r}) — check the port-forward"
        )

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
