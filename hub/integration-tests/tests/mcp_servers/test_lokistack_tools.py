import json
import os
import subprocess
import time
import uuid

import pytest

NAMESPACE = os.environ.get("NAMESPACE", "hub")
_LOG_WAIT_TIMEOUT = int(os.environ.get("LOG_WAIT_TIMEOUT", "60"))
_LOG_WAIT_POLL = 5


@pytest.fixture(scope="session", autouse=True)
def wait_for_log_generator_logs(mcp_lokistack_client):
    """Wait for log generator pod to be Ready, then poll until logs land in LokiStack."""
    proc = subprocess.run(
        [
            "oc",
            "wait",
            "--for=condition=Ready",
            "pod",
            "-l",
            "app=log-generator",
            "-n",
            NAMESPACE,
            f"--timeout={_LOG_WAIT_TIMEOUT}s",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(f"log-generator pod not ready: {proc.stderr.strip() or proc.stdout.strip()}")
    deadline = time.time() + _LOG_WAIT_TIMEOUT
    while time.time() < deadline:
        try:
            result = _mcp_call(
                mcp_lokistack_client,
                "search_logs_regex",
                {"namespace": NAMESPACE, "duration": "5m", "regex": "LOKITEST"},
            )
            if result.get("count", 0) > 0:
                return
        except Exception:
            pass
        time.sleep(_LOG_WAIT_POLL)
    pytest.fail("Log generator logs did not appear in LokiStack within timeout")


def _mcp_call(client, tool_name, arguments=None):
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }
    resp = client.post(
        "/mcp",
        json=payload,
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200
    if "text/event-stream" in resp.headers.get("content-type", ""):
        body = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    body = json.loads(data)
        assert body is not None, "No data in SSE response"
    else:
        body = resp.json()
    assert "error" not in body, f"MCP error: {body.get('error')}"
    content = body["result"]["content"]
    assert len(content) > 0
    return json.loads(content[0]["text"])


class TestSearchLogs:
    def test_returns_logs(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "search_logs_regex",
            {"namespace": NAMESPACE, "duration": "5m", "regex": "LOKITEST"},
        )
        assert "query" in result
        assert result["count"] > 0
        assert any("LOKITEST" in log.get("line", "") for log in result["logs"])

    def test_search_logs_text(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "search_logs",
            {"namespace": NAMESPACE, "duration": "5m", "text": "LOKITEST"},
        )
        assert result["count"] > 0
        assert any("LOKITEST" in log.get("line", "") for log in result["logs"])

    def test_invalid_tenant_returns_error(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "search_logs",
            {"namespace": "test", "tenant": "bad"},
        )
        assert result["success"] is False
        assert "Invalid tenant" in result["error"]


class TestQueryLogql:
    def test_raw_query_returns_logs(self, mcp_lokistack_client):
        logql = f'{{kubernetes_namespace_name="{NAMESPACE}"}}' ' |~ "LOKITEST"'
        result = _mcp_call(
            mcp_lokistack_client,
            "query_logql",
            {"logql_query": logql, "duration": "5m"},
        )
        assert result["count"] > 0
        assert any("LOKITEST" in log.get("line", "") for log in result["logs"])

    def test_invalid_query_returns_error(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "query_logql",
            {"logql_query": "no stream selector here"},
        )
        assert result["success"] is False


class TestQueryMetrics:
    def test_error_rate(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "query_metrics",
            {
                "metric_type": "error_rate",
                "namespace": NAMESPACE,
                "duration": "5m",
            },
        )
        assert result["metric_type"] == "error_rate"
        assert "total" in result

    def test_log_volume(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "query_metrics",
            {
                "metric_type": "log_volume",
                "namespace": NAMESPACE,
                "duration": "5m",
            },
        )
        assert result["metric_type"] == "log_volume"
        assert result["total"] >= 0

    def test_invalid_metric_type(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "query_metrics",
            {"metric_type": "bad"},
        )
        assert result["success"] is False


class TestFindErrorPatterns:
    def test_returns_patterns(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "find_error_patterns",
            {
                "namespace": NAMESPACE,
                "duration": "5m",
            },
        )
        assert "patterns" in result
        assert result["total_errors"] > 0

    def test_multiple_severity_patterns(self, mcp_lokistack_client):
        result = _mcp_call(
            mcp_lokistack_client,
            "find_error_patterns",
            {
                "namespace": NAMESPACE,
                "duration": "5m",
            },
        )
        assert result["pattern_count"] >= 2
