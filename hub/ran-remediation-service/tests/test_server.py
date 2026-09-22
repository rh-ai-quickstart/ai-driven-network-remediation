"""Unit tests for FastAPI server endpoints."""

import asyncio
import json
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ran_remediation_service.server import _handle_enriched_message, app


def _app_with_state(connected: bool = True, results: list | None = None):
    app.state.recent_results = deque(results or [], maxlen=100)
    mock_consumer = MagicMock()
    mock_consumer.is_connected = connected
    app.state.kafka_consumer = mock_consumer
    return app


@pytest.fixture()
def client_ok():
    return TestClient(_app_with_state(connected=True), raise_server_exceptions=True)


@pytest.fixture()
def client_no_kafka():
    return TestClient(_app_with_state(connected=False), raise_server_exceptions=True)


def test_health(client_ok):
    resp = client_ok.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_when_kafka_connected(client_ok):
    with patch("ran_remediation_service.server.KAFKA_CONSUMER_ENABLED", True):
        resp = client_ok.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


def test_ready_when_kafka_disconnected(client_no_kafka):
    with patch("ran_remediation_service.server.KAFKA_CONSUMER_ENABLED", True):
        resp = client_no_kafka.get("/ready")
    assert resp.status_code == 503
    assert "kafka" in resp.json()["reason"]


def test_remediation_results_empty(client_ok):
    resp = client_ok.get("/remediation-results")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["results"] == []


def test_remediation_results_returns_buffer(client_ok):
    record = {"incident_id": "a3f7c2d1", "zone": "A", "application": "File", "success": True}
    _app_with_state(connected=True, results=[record])
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/remediation-results")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert resp.json()["results"][0]["incident_id"] == "a3f7c2d1"


def test_remediation_results_limit(client_ok):
    records = [{"incident_id": f"id-{i}", "success": True} for i in range(10)]
    _app_with_state(connected=True, results=records)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/remediation-results?limit=3")
    assert resp.json()["count"] == 3


_VALID_MESSAGE = {
    "incident_id": "a3f7c2d1",
    "zone": "A",
    "application": "File",
    "ad_label": "anomalous",
    "ad_confidence": 0.9995,
    "root_cause": "Signal degradation consistent with antenna misalignment.",
    "recommended_fix": "Adjust antenna tilt per vendor guide Section 4.3.2.",
}


def _make_graph(success: bool = True):
    mock = MagicMock()
    mock.ainvoke = AsyncMock(return_value={
        "incident_id": "a3f7c2d1",
        "zone": "A",
        "application": "File",
        "template_name": "ran-antenna-tilt-adjust",
        "job_id": "7",
        "job_status": "successful",
        "success": success,
        "output_summary": "ok=3",
        "timestamp": "2026-09-22T10:00:00Z",
    })
    return mock


def test_handle_enriched_message_success():
    graph = _make_graph(success=True)
    buffer: deque = deque(maxlen=10)
    raw = json.dumps(_VALID_MESSAGE).encode()
    _handle_enriched_message(raw, graph, buffer)
    assert len(buffer) == 1
    assert buffer[0]["incident_id"] == "a3f7c2d1"
    assert buffer[0]["success"] is True


def test_handle_enriched_message_failed_graph():
    graph = _make_graph(success=False)
    buffer: deque = deque(maxlen=10)
    raw = json.dumps(_VALID_MESSAGE).encode()
    _handle_enriched_message(raw, graph, buffer)
    assert buffer[0]["success"] is False


def test_handle_enriched_message_malformed_json():
    graph = _make_graph()
    buffer: deque = deque(maxlen=10)
    _handle_enriched_message(b"not-json", graph, buffer)
    assert len(buffer) == 0


def test_handle_enriched_message_graph_exception():
    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph crashed"))
    buffer: deque = deque(maxlen=10)
    raw = json.dumps(_VALID_MESSAGE).encode()
    _handle_enriched_message(raw, graph, buffer)
    assert len(buffer) == 0
