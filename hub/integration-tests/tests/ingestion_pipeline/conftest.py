import os

import httpx
import pytest


@pytest.fixture(scope="session")
def ingestion_client():
    base_url = os.environ.get("INGESTION_PIPELINE_URL", "http://localhost:8000")
    with httpx.Client(base_url=base_url) as client:
        yield client
