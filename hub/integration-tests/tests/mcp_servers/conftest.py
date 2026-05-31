import os

import httpx
import pytest

_lokistack_enabled = os.environ.get("ENABLE_LOKISTACK", "false").lower() == "true"


@pytest.fixture(scope="session")
def mcp_openshift_client():
    base_url = os.environ.get("MCP_OPENSHIFT_URL", "http://localhost:8001")
    with httpx.Client(base_url=base_url) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_lokistack_client():
    if not _lokistack_enabled:
        pytest.skip("LokiStack is disabled (ENABLE_LOKISTACK != true)")
    base_url = os.environ.get("MCP_LOKISTACK_URL", "http://localhost:8002")
    with httpx.Client(base_url=base_url) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_kafka_client():
    base_url = os.environ.get("MCP_KAFKA_URL", "http://localhost:8003")
    with httpx.Client(base_url=base_url) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_aap_client():
    base_url = os.environ.get("MCP_AAP_URL", "http://localhost:8004")
    with httpx.Client(base_url=base_url) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_slack_client():
    base_url = os.environ.get("MCP_SLACK_URL", "http://localhost:8005")
    with httpx.Client(base_url=base_url) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_servicenow_client():
    base_url = os.environ.get("MCP_SERVICENOW_URL", "http://localhost:8006")
    with httpx.Client(base_url=base_url) as client:
        yield client
