"""Unit tests for chatbot BFF endpoints."""

from unittest.mock import AsyncMock, patch


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "noc-chatbot-bff"
    assert "version" in data


@patch("chatbot_service.fetch_servicenow_incident_count", new_callable=AsyncMock)
def test_summary(mock_snow, client):
    mock_snow.return_value = (3, {"mode": "mock", "reachable": True})
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert data["agent_status"] == "running"
    assert data["cluster"] == "hub"
    assert data["site"] == "edge-01"
    assert data["open_incidents"] == 3
    assert data["servicenow"] == {"mode": "mock", "reachable": True}
    assert "timestamp" in data


@patch("chatbot_service.fetch_servicenow_incident_count", new_callable=AsyncMock)
def test_summary_servicenow_unreachable(mock_snow, client):
    mock_snow.return_value = (0, {"mode": "mock", "reachable": False})
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "degraded", "unavailable": ["servicenow"]}
    assert data["open_incidents"] == 0


@patch("chatbot_service.fetch_recent_audits")
@patch("chatbot_service.probe_http", new_callable=AsyncMock)
def test_integrations(mock_probe, mock_audits, client):
    mock_probe.return_value = {"status": "up", "http_code": 200, "reachable": True}
    mock_audits.return_value = ([], True)
    resp = client.get("/api/integrations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert data["total"] == 6
    assert data["up"] == 6
    assert data["down"] == 0
    assert "slo" in data
    assert "incident_movie" in data
    assert "business_impact" in data
    assert "integrations" in data
    assert len(data["integrations"]) == 6


@patch("chatbot_service.fetch_recent_audits")
@patch("chatbot_service.probe_http", new_callable=AsyncMock)
def test_integrations_with_down_service(mock_probe, mock_audits, client):
    async def side_effect(url, timeout=4.0):
        if "openshift" in url:
            return {"status": "down", "http_code": None, "reachable": False}
        return {"status": "up", "http_code": 200, "reachable": True}

    mock_probe.side_effect = side_effect
    mock_audits.return_value = ([], True)
    resp = client.get("/api/integrations?force_refresh=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "degraded", "unavailable": ["probes"]}
    assert data["up"] == 5
    assert data["down"] == 1


@patch("chatbot_service.fetch_recent_audits")
@patch("chatbot_service.probe_http", new_callable=AsyncMock)
def test_integrations_kafka_unreachable(mock_probe, mock_audits, client):
    mock_probe.return_value = {"status": "up", "http_code": 200, "reachable": True}
    mock_audits.return_value = ([], False)
    resp = client.get("/api/integrations?force_refresh=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "degraded", "unavailable": ["kafka"]}
    assert data["up"] == 6


@patch("chatbot_service.publish_demo_event")
def test_demo_trigger(mock_publish, client):
    mock_publish.return_value = 42
    resp = client.post("/api/demo/trigger", json={"scenario": "oom", "site": "edge-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert data["status"] == "queued"
    assert data["scenario"] == "oom"
    assert data["kafka_offset"] == 42
    assert "incident_id" in data
    assert "event_message" in data
    assert "OOMKilled" in data["event_message"]


@patch("chatbot_service.publish_demo_event")
def test_demo_trigger_crashloop(mock_publish, client):
    mock_publish.return_value = 10
    resp = client.post("/api/demo/trigger", json={"scenario": "crashloop", "site": "edge-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert data["scenario"] == "crashloop"
    assert "CrashLoopBackOff" in data["event_message"]


@patch("chatbot_service.publish_demo_event")
def test_demo_trigger_kafka_failure(mock_publish, client):
    mock_publish.side_effect = Exception("Kafka unreachable")
    resp = client.post("/api/demo/trigger", json={"scenario": "oom", "site": "edge-01"})
    assert resp.status_code == 502
    data = resp.json()
    assert data["status"] == "error"
    assert "Kafka unreachable" in data["error"]


@patch("chatbot_service.get_integrations", new_callable=AsyncMock)
@patch("chatbot_service.fetch_servicenow_incident_count", new_callable=AsyncMock)
@patch("chatbot_service.call_model", new_callable=AsyncMock)
def test_chat(mock_model, mock_snow, mock_integrations, client):
    mock_snow.return_value = (1, {"mode": "mock", "reachable": True})
    mock_integrations.return_value = {
        "_deps": {"status": "ok"},
        "total": 7,
        "up": 7,
        "down": 0,
        "integrations": [
            {"id": "mcp-openshift", "name": "MCP OpenShift", "group": "mcp", "status": "up", "http_code": 200},
        ],
        "slo": {},
        "incident_movie": [],
        "business_impact": {},
    }
    mock_model.return_value = ("Root cause is OOM on nginx.", "live")

    resp = client.post("/api/chat", json={"message": "What happened?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert "reply" in data
    assert data["model"]["name"] == "granite-4-h-tiny"
    assert data["model"]["source"] == "live"
    assert "session_id" in data
    assert data["context"]["open_incidents"] == 1


@patch("chatbot_service.get_integrations", new_callable=AsyncMock)
@patch("chatbot_service.fetch_servicenow_incident_count", new_callable=AsyncMock)
@patch("chatbot_service.call_model", new_callable=AsyncMock)
def test_chat_model_unavailable(mock_model, mock_snow, mock_integrations, client):
    mock_snow.return_value = (0, {"mode": "mock", "reachable": True})
    mock_integrations.return_value = {
        "_deps": {"status": "ok"},
        "total": 7,
        "up": 5,
        "down": 2,
        "integrations": [],
        "slo": {},
        "incident_movie": [],
        "business_impact": {},
    }
    mock_model.return_value = ("", "unreachable")

    resp = client.post("/api/chat", json={"message": "Status?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "degraded", "unavailable": ["llm"]}
    assert "model unavailable" in data["reply"].lower() or "fallback" in data["reply"].lower()
    assert data["model"]["source"] == "unreachable"


def test_chat_empty_message(client):
    resp = client.post("/api/chat", json={"message": "  "})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Please enter a question."
