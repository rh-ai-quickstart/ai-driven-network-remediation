import os
import time

import httpx
import pytest

_SERVICE_READY_TIMEOUT = int(os.environ.get("SERVICE_READY_TIMEOUT", "90"))


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
