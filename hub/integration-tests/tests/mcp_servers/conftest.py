import json
import os
import time
from dataclasses import dataclass

import httpx
import pytest

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def parse_sse_json(response) -> dict:
    """Parse a JSON-RPC result from either plain JSON or SSE response."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                data = line.removeprefix("data:").strip()
                if data:
                    return json.loads(data)
        raise ValueError(f"No data line in SSE response: {response.text}")
    return response.json()


def mcp_call(client, tool_name: str, arguments=None) -> dict:
    """Call an MCP tool via JSON-RPC and return the parsed result."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        },
        headers=MCP_HEADERS,
    )
    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text}"
    data = parse_sse_json(response)
    assert "result" in data, f"No result in response: {data}"
    result = data["result"]
    content = result["content"]
    assert len(content) > 0
    text = content[0]["text"]
    if result.get("isError"):
        return {"success": False, "error": text}
    return json.loads(text)


def mcp_list_tools(client) -> set[str]:
    """Return the set of tool names from the MCP tools/list endpoint."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        },
        headers=MCP_HEADERS,
    )
    assert response.status_code == 200
    data = parse_sse_json(response)
    return {t["name"] for t in data.get("result", {}).get("tools", [])}


_lokistack_enabled = os.environ.get("ENABLE_LOKISTACK", "false").lower() == "true"

_BASE_HOST = "http://localhost"
_SERVICE_READY_TIMEOUT = int(os.environ.get("SERVICE_READY_TIMEOUT", "90"))


@dataclass(frozen=True)
class _ServiceCfg:
    port: int
    timeout: int

    @property
    def base_url(self) -> str:
        return f"{_BASE_HOST}:{self.port}"


_SERVICES: dict[str, _ServiceCfg] = {
    "openshift": _ServiceCfg(port=8001, timeout=30),
    "lokistack": _ServiceCfg(port=8002, timeout=30),
    "kafka": _ServiceCfg(port=8003, timeout=60),
    "aap": _ServiceCfg(port=8004, timeout=30),
    "servicenow": _ServiceCfg(port=8006, timeout=30),
}


def _wait_for_service(svc: _ServiceCfg, name: str) -> None:
    """Poll /health until the service responds 200 or timeout expires."""
    deadline = time.monotonic() + _SERVICE_READY_TIMEOUT
    backoff = 1
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{svc.base_url}/health", timeout=5)
            if resp.status_code == 200:
                return
            last_err = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last_err = str(exc)
        time.sleep(backoff)
        backoff = min(backoff * 2, 8)
    pytest.fail(f"{name} ({svc.base_url}) not healthy " f"after {_SERVICE_READY_TIMEOUT}s: {last_err}")


@pytest.fixture(scope="session", autouse=True)
def _wait_for_all_services():
    """Block the test session until every enabled MCP service is healthy."""
    for name, svc in _SERVICES.items():
        if name == "lokistack" and not _lokistack_enabled:
            continue
        _wait_for_service(svc, name)


_MAX_RERUN_DELAY = 32


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Apply exponential backoff between reruns for flaky-marked tests."""
    if not item.get_closest_marker("flaky"):
        return
    count = getattr(item, "execution_count", 0)
    if count > 0:
        delay = min(2**count, _MAX_RERUN_DELAY)
        time.sleep(delay)


def _make_client(name: str) -> httpx.Client:
    svc = _SERVICES[name]
    return httpx.Client(base_url=svc.base_url, timeout=svc.timeout)


@pytest.fixture(scope="session")
def mcp_openshift_client():
    with _make_client("openshift") as client:
        yield client


@pytest.fixture(scope="session")
def mcp_lokistack_client():
    if not _lokistack_enabled:
        pytest.skip("LokiStack is disabled (ENABLE_LOKISTACK != true)")
    with _make_client("lokistack") as client:
        yield client


@pytest.fixture(scope="session")
def mcp_kafka_client():
    with _make_client("kafka") as client:
        yield client


@pytest.fixture(scope="session")
def mcp_aap_client():
    with _make_client("aap") as client:
        yield client


@pytest.fixture(scope="session")
def mcp_servicenow_client():
    with _make_client("servicenow") as client:
        yield client
