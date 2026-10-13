import os

import httpx
import pytest


@pytest.fixture(scope="session")
def ran_ml_service_client():
    base_url = os.environ.get("RAN_ML_SERVICE_URL", "http://localhost:8009")
    token = os.environ.get("RAN_ML_SERVICE_TOKEN")
    required = os.environ.get("RAN_ML_SERVICE_REQUIRED", "false").lower() == "true"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = httpx.get(f"{base_url}/health", headers=headers, timeout=5.0)
    except (httpx.ConnectError, httpx.TimeoutException):
        if required:
            pytest.fail(f"ran-ml-service not reachable at {base_url}")
        pytest.skip(
            f"ran-ml-service not reachable at {base_url} "
            "(set RAN_ML_SERVICE_REQUIRED=true to require it)"
        )

    # A 200 alone does not prove this is ran-ml-service — any service sharing the port
    # answers /health. Only ran-ml-service reports a "task". A reachable but misrouted
    # port is a broken setup, not an absent optional service, so always fail here.
    try:
        task = response.json().get("task")
    except ValueError:
        task = None
    if task is None:
        pytest.fail(
            f"{base_url}/health did not answer as ran-ml-service "
            f"(HTTP {response.status_code}, body {response.text[:200]!r}) — check the port-forward"
        )

    with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
        yield client
