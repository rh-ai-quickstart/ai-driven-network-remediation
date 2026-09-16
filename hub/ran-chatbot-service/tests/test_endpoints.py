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


@patch("ran_chatbot_service.probe_http", new_callable=AsyncMock)
def test_ready_llm_unreachable(mock_probe, client):
    mock_probe.return_value = {"status": "down", "http_code": None, "reachable": False}
    client.app.state.kafka_consumer.is_connected = True

    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["llm"] is False


@patch("ran_chatbot_service.probe_http", new_callable=AsyncMock)
def test_ready_kafka_unreachable(mock_probe, client):
    mock_probe.return_value = {"status": "up", "http_code": 200, "reachable": True}
    client.app.state.kafka_consumer.is_connected = False

    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["kafka"] is False


def test_anomalies_empty_buffer(client):
    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["anomalies"] == []
    assert "_deps" in data


def test_anomalies_returns_most_recent_first(client, sample_anomaly):
    oldest = sample_anomaly.model_copy(update={"cell_id": 1})
    newest = sample_anomaly.model_copy(update={"cell_id": 2})
    client.app.state.recent_anomalies.append(oldest)
    client.app.state.recent_anomalies.append(newest)

    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert [a["cell_id"] for a in data["anomalies"]] == [2, 1]


def test_anomalies_includes_root_cause_and_recommended_fix(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)

    resp = client.get("/api/anomalies")
    data = resp.json()
    assert data["anomalies"][0]["root_cause"] == sample_anomaly.root_cause
    assert data["anomalies"][0]["recommended_fix"] == sample_anomaly.recommended_fix


def test_anomalies_deps_ok_when_kafka_connected(client):
    client.app.state.kafka_consumer.is_connected = True
    resp = client.get("/api/anomalies")
    assert resp.json()["_deps"] == {"status": "ok"}


def test_anomalies_deps_degraded_when_kafka_down(client):
    client.app.state.kafka_consumer.is_connected = False
    resp = client.get("/api/anomalies")
    assert resp.json()["_deps"] == {"status": "degraded", "unavailable": ["kafka"]}


def test_clear_anomalies_empties_the_buffer(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)
    client.app.state.recent_anomalies.append(sample_anomaly)

    resp = client.delete("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cleared"
    assert data["count"] == 0
    assert "_deps" in data

    # The buffer itself is empty now, not just the reported count.
    follow_up = client.get("/api/anomalies")
    assert follow_up.json()["count"] == 0


def test_clear_anomalies_on_empty_buffer_is_a_noop(client):
    resp = client.delete("/api/anomalies")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_clear_anomalies_deps_reflects_kafka_status(client):
    client.app.state.kafka_consumer.is_connected = False
    resp = client.delete("/api/anomalies")
    assert resp.json()["_deps"] == {"status": "degraded", "unavailable": ["kafka"]}


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_low_signal(mock_publish, client):
    mock_publish.return_value = 7
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/demo/trigger", json={"scenario": "low_signal"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert data["status"] == "queued"
    assert data["scenario"] == "low_signal"
    assert data["cell_id"] == 9001
    assert data["band"] == "Band 71"
    assert data["topic"] == "ran-combined-metrics"
    assert data["kafka_offset"] == 7


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_cell_outage(mock_publish, client):
    mock_publish.return_value = 1
    resp = client.post("/api/demo/trigger", json={"scenario": "cell_outage"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario"] == "cell_outage"
    assert data["cell_id"] == 9002


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_defaults_to_low_signal(mock_publish, client):
    mock_publish.return_value = 0
    resp = client.post("/api/demo/trigger", json={})
    assert resp.status_code == 200
    assert resp.json()["scenario"] == "low_signal"


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_kafka_failure_reported_as_502(mock_publish, client):
    mock_publish.side_effect = Exception("Kafka unreachable")
    resp = client.post("/api/demo/trigger", json={"scenario": "low_signal"})
    assert resp.status_code == 502
    data = resp.json()
    assert data["status"] == "error"
    assert data["scenario"] == "low_signal"
    assert data["cell_id"] == 9001


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_failure_does_not_leak_exception_details(mock_publish, client):
    """Regression test: the response must not expose str(exc), which could
    contain internal infra details like broker addresses/DNS names — only a
    generic message (the real exception is captured via logger.exception)."""
    mock_publish.side_effect = Exception("Connection to broker kafka-internal.svc:9092 failed")
    resp = client.post("/api/demo/trigger", json={"scenario": "low_signal"})
    data = resp.json()
    assert data["error"] == "Failed to publish demo metrics to Kafka"
    assert "kafka-internal.svc" not in data["error"]


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat(mock_model, client, sample_anomalies):
    mock_model.return_value = ("Cell 42 has weak signal due to distance from the antenna.", "live")
    client.app.state.recent_anomalies.extend(sample_anomalies)
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/chat", json={"message": "What's wrong with cell 42?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert "reply" in data
    assert "name" in data["model"]
    assert data["model"]["source"] == "live"
    assert "session_id" in data
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
    assert data["model"]["source"] == "unreachable"


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat_model_http_error_reported_as_degraded(mock_model, client, sample_anomalies):
    """Regression test: an HTTP error from the LLM (e.g. a 404 for an unregistered
    model) must be reported as degraded, not silently treated as healthy."""
    mock_model.return_value = ("", "http-404")
    client.app.state.recent_anomalies.extend(sample_anomalies)
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/chat", json={"message": "Status?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "degraded", "unavailable": ["llm"]}
    assert data["model"]["source"] == "http-404"


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat_kafka_unreachable(mock_model, client):
    mock_model.return_value = ("insight", "live")
    client.app.state.kafka_consumer.is_connected = False

    resp = client.post("/api/chat", json={"message": "Status?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "degraded", "unavailable": ["kafka"]}
    assert data["context"]["anomaly_count"] == 0


def test_chat_empty_message(client):
    resp = client.post("/api/chat", json={"message": "  "})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Please enter a question."


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat_preserves_session_history(mock_model, client, sample_anomalies):
    mock_model.return_value = ("ok", "live")
    client.app.state.recent_anomalies.extend(sample_anomalies)
    client.app.state.kafka_consumer.is_connected = True
    session_id = "test-session-1"

    first = client.post("/api/chat", json={"message": "hello", "session_id": session_id})
    second = client.post("/api/chat", json={"message": "follow up", "session_id": session_id})

    assert first.json()["session_id"] == session_id
    assert second.json()["session_id"] == session_id
