import os

import httpx
import pytest


@pytest.fixture(scope="session")
def agent_service_client():
    base_url = os.environ.get("AGENT_SERVICE_URL", "http://localhost:8007")
    with httpx.Client(base_url=base_url, timeout=120) as client:
        yield client
