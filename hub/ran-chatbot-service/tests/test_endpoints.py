"""Unit tests for the RAN chatbot BFF endpoints."""

from unittest.mock import AsyncMock, patch


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "ran-chatbot-bff"
    assert "version" in data


@patch("ran_chatbot_service.probe_http", new_callable=AsyncMock)
def test_ready_all_up(mock_probe, client):
    mock_probe.return_value = {"status": "up", "http_code": 200, "reachable": True}
    client.app.state.kafka_consumer.is_connected = True

    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["checks"] == {"kafka": True, "llm": True}


def test_anomalies_empty_buffer(client):
    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["anomalies"] == []
    assert "_deps" in data


def test_anomalies_returns_most_recent_first(client, sample_anomaly):
    oldest = sample_anomaly.model_copy(update={"incident_id": "inc-001"})
    newest = sample_anomaly.model_copy(update={"incident_id": "inc-002"})
    client.app.state.recent_anomalies.append(oldest)
    client.app.state.recent_anomalies.append(newest)

    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert [a["incident_id"] for a in data["anomalies"]] == ["inc-002", "inc-001"]


def test_anomalies_includes_new_schema_fields(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)

    resp = client.get("/api/anomalies")
    data = resp.json()
    anomaly = data["anomalies"][0]
    assert "incident_id" in anomaly
    assert "zone" in anomaly
    assert "application" in anomaly
    assert "ad_label" in anomaly
    assert "ad_confidence" in anomaly
    assert "root_cause" in anomaly
    assert "recommended_fix" in anomaly
    assert "cell_id" not in anomaly
    assert "band" not in anomaly


def test_clear_anomalies_empties_the_buffer(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)

    resp = client.delete("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cleared"
    assert data["count"] == 0

    follow_up = client.get("/api/anomalies")
    assert follow_up.json()["count"] == 0


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_antenna_failure(mock_publish, client):
    mock_publish.return_value = 7
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/demo/trigger", json={"scenario": "antenna_failure"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert data["status"] == "queued"
    assert data["scenario"] == "antenna_failure"
    assert "incident_id" in data
    assert data["topic"] == "ran-combined-metrics"
    assert data["kafka_offset"] == 7
    assert "cell_id" not in data
    assert "band" not in data


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_defaults_to_antenna_failure(mock_publish, client):
    mock_publish.return_value = 0
    resp = client.post("/api/demo/trigger", json={})
    assert resp.status_code == 200
    assert resp.json()["scenario"] == "antenna_failure"


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_kafka_failure_reported_as_502(mock_publish, client):
    mock_publish.side_effect = Exception("Kafka unreachable")
    resp = client.post("/api/demo/trigger", json={"scenario": "antenna_failure"})
    assert resp.status_code == 502
    data = resp.json()
    assert data["status"] == "error"
    assert data["scenario"] == "antenna_failure"
    assert "incident_id" in data


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat(mock_model, client, sample_anomalies):
    mock_model.return_value = ("Incident test-001 shows signal degradation in zone A.", "live")
    client.app.state.recent_anomalies.extend(sample_anomalies)
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/chat", json={"message": "What's wrong?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert "reply" in data
    assert data["model"]["source"] == "live"
    assert data["context"]["anomaly_count"] > 0


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat_model_unavailable(mock_model, client, sample_anomalies):
    mock_model.return_value = ("", "unreachable")
    client.app.state.recent_anomalies.extend(sample_anomalies)
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/chat", json={"message": "Status?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "degraded", "unavailable": ["llm"]}
    assert "fallback" in data["reply"].lower()


def test_chat_empty_message(client):
    resp = client.post("/api/chat", json={"message": "  "})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Please enter a question."
