import os
import time

import httpx
import pytest

from tests.generic.mcp_servers.conftest import mcp_call

_SERVICE_READY_TIMEOUT = int(os.environ.get("SERVICE_READY_TIMEOUT", "90"))
_DEFAULT_HUB_SPOKE_SITE_ID = "edge-01"


def _wait_for_health(base_url: str) -> None:
    deadline = time.monotonic() + _SERVICE_READY_TIMEOUT
    backoff = 1
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=5)
            if resp.status_code == 200:
                return
            last_err = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last_err = str(exc)
        time.sleep(backoff)
        backoff = min(backoff * 2, 8)
    pytest.fail(f"mcp-noc-openshift ({base_url}) not healthy after {_SERVICE_READY_TIMEOUT}s: {last_err}")


@pytest.fixture(scope="session")
def mcp_openshift_client():
    base_url = "http://localhost:8001"
    _wait_for_health(base_url)
    with httpx.Client(base_url=base_url, timeout=30) as client:
        yield client


def _is_hub_spoke_probe_error(result: dict) -> bool:
    error = result.get("error", "")
    return "unspecified-edge-site" in error


@pytest.fixture(scope="session")
def edge_site_id(mcp_openshift_client) -> str:
    """Alert site label for hub-spoke MCP calls (edge-NN). Empty in single-cluster."""
    configured = os.environ.get("EDGE_SITE_ID", "").strip()
    if configured:
        return configured

    probe = mcp_call(mcp_openshift_client, "get_namespaces")
    if _is_hub_spoke_probe_error(probe):
        return _DEFAULT_HUB_SPOKE_SITE_ID
    return ""


@pytest.fixture(scope="session")
def mcp_openshift_tool_args(edge_site_id: str):
    """Merge edge_site_id into MCP tool arguments when hub-spoke is active."""

    def _args(**kwargs):
        if edge_site_id:
            kwargs.setdefault("edge_site_id", edge_site_id)
        return kwargs

    return _args
