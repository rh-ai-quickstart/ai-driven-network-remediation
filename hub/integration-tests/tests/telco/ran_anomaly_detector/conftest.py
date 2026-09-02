import os

import httpx
import pytest


@pytest.fixture(scope="session")
def ran_anomaly_detector_client():
    base_url = os.environ.get("RAN_ANOMALY_DETECTOR_URL", "http://localhost:8002")
    try:
        httpx.get(f"{base_url}/health", timeout=5.0)
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip(f"ran-anomaly-detector not reachable at {base_url} (requires port-forward)")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
