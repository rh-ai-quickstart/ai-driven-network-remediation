"""Integration tests for the Telco O-RAN chatbot BFF service (ran-chatbot-service).

These run against a deployed ran-chatbot-service (via port-forward or direct URL).
Set RAN_CHATBOT_SERVICE_URL env var to override the default http://localhost:8008.

Tests the ML-based detection pipeline contract: demo trigger injects TelecomTS
fixture samples, anomalies use incident_id/zone/application/ad_confidence
(not cell_id/band/anomaly_type).
"""

import pytest

pytestmark = pytest.mark.telco


def test_health(ran_chatbot_client):
    """Service is alive and reports correct identity."""
    response = ran_chatbot_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "ran-chatbot-bff"
    assert "version" in data


def test_ready(ran_chatbot_client):
    """Readiness probe reports Kafka + LLM dependency status but always passes."""
    response = ran_chatbot_client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "kafka" in data["checks"]
    assert "llm" in data["checks"]


def test_chat(ran_chatbot_client):
    """Chat endpoint accepts a message and returns a structured reply."""
    response = ran_chatbot_client.post(
        "/api/chat",
        json={"message": "Are there any anomalies?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    assert len(data["reply"]) > 0
    assert "session_id" in data
    assert "model" in data
    assert data["model"]["name"]
    assert data["model"]["source"]
    assert "context" in data
    assert "anomaly_count" in data["context"]
    assert "_deps" in data
    assert data["_deps"]["status"] in {"ok", "degraded"}


def test_chat_empty_message(ran_chatbot_client):
    """Chat endpoint handles empty message gracefully."""
    response = ran_chatbot_client.post("/api/chat", json={"message": "   "})
    assert response.status_code == 200
    assert response.json()["reply"] == "Please enter a question."


def test_anomalies_endpoint_schema(ran_chatbot_client):
    """Anomalies endpoint returns the new ML-based schema (incident_id, not cell_id)."""
    response = ran_chatbot_client.get("/api/anomalies")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "anomalies" in data
    assert data["count"] == len(data["anomalies"])
    assert "_deps" in data
    for anomaly in data["anomalies"]:
        assert "incident_id" in anomaly
        assert "zone" in anomaly
        assert "application" in anomaly
        assert "ad_label" in anomaly
        assert "ad_confidence" in anomaly
        assert "root_cause" in anomaly
        assert "recommended_fix" in anomaly
        assert "cell_id" not in anomaly
        assert "band" not in anomaly


def test_demo_trigger_antenna_failure(ran_chatbot_client):
    """Demo trigger injects a TelecomTS fixture and returns new identity fields."""
    response = ran_chatbot_client.post("/api/demo/trigger", json={"scenario": "antenna_failure"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["scenario"] == "antenna_failure"
    assert "incident_id" in data
    assert data["topic"] == "ran-combined-metrics"
    assert isinstance(data["kafka_offset"], int)
    assert "_deps" in data
    assert "cell_id" not in data
    assert "band" not in data


def test_demo_trigger_normal_traffic(ran_chatbot_client):
    """Normal traffic scenario should also publish (detector decides whether to flag)."""
    response = ran_chatbot_client.post("/api/demo/trigger", json={"scenario": "normal_traffic"})
    assert response.status_code == 200
    data = response.json()
    assert data["scenario"] == "normal_traffic"
    assert "incident_id" in data


def test_demo_trigger_unknown_scenario_falls_back(ran_chatbot_client):
    response = ran_chatbot_client.post("/api/demo/trigger", json={"scenario": "not-a-real-scenario"})
    assert response.status_code == 200
    assert response.json()["scenario"] == "antenna_failure"


def test_chat_preserves_session_history(ran_chatbot_client):
    """Two chat requests with the same session_id are tracked as one conversation."""
    session_id = "integration-test-session-ml"

    first = ran_chatbot_client.post(
        "/api/chat",
        json={"message": "Any anomalies?", "session_id": session_id},
    )
    second = ran_chatbot_client.post(
        "/api/chat",
        json={"message": "Tell me more", "session_id": session_id},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["session_id"] == session_id
    assert second.json()["session_id"] == session_id


@pytest.mark.order(after="test_demo_trigger_antenna_failure")
def test_clear_anomalies(ran_chatbot_client):
    """Clearing the buffer empties it immediately."""
    response = ran_chatbot_client.delete("/api/anomalies")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "cleared"
    assert data["count"] == 0

    follow_up = ran_chatbot_client.get("/api/anomalies")
    assert follow_up.json()["count"] == 0
