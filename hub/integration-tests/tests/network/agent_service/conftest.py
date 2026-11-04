import os

import httpx
import pytest


@pytest.fixture(scope="session")
def agent_service_client():
    base_url = os.environ.get("AGENT_SERVICE_URL", "http://localhost:8007")
    timeout_seconds = float(os.environ.get("AGENT_SERVICE_TIMEOUT_SECONDS", "2400"))
    with httpx.Client(base_url=base_url, timeout=timeout_seconds) as client:
        yield client
