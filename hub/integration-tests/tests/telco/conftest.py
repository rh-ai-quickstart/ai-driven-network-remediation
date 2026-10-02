import os

import httpx
import pytest


@pytest.fixture(scope="session")
def ran_chatbot_client():
    base_url = os.environ.get("RAN_CHATBOT_SERVICE_URL", "http://localhost:8008")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
