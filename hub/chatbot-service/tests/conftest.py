from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

TEST_ENVIRONMENT = {
    "EDGE_NAMESPACE": "dark-noc-edge",
    "EDGE_SITE_ID": "edge-site-01",
    "CPU_DEMO_SITE_A": "edge-site-01",
    "CPU_DEMO_SITE_B": "edge-site-02",
    "DEMO_WORKLOAD": "edge-nginx",
}

# Configuration and request defaults capture environment values during import.
with patch.dict("os.environ", TEST_ENVIRONMENT, clear=True):
    from chatbot_service import app


@pytest.fixture()
def client():
    return TestClient(app)
