from fastapi.testclient import TestClient

from agent_service.models import IncidentState
from agent_service.server import app

INCIDENT_STATE_FIELDS = set(IncidentState.model_fields.keys())

client = TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestReadyEndpoint:
    def test_ready_returns_true(self):
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"ready": True}


class TestRemediateEndpoint:
    def test_post_remediate_returns_full_state(self):
        response = client.post("/remediate", json={"raw_event": "test event"})
        assert response.status_code == 200
        body = response.json()
        assert body["raw_event"] == "test event"
        assert set(body.keys()) == INCIDENT_STATE_FIELDS
        assert body["decision"] != ""

    def test_post_remediate_with_failure_type_override(self):
        response = client.post(
            "/remediate",
            json={"raw_event": "test event", "confidence_override": 0.9, "failure_type_override": "KafkaLag"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "lightspeed"

    def test_post_remediate_rejects_invalid_failure_type_override(self):
        response = client.post(
            "/remediate",
            json={"raw_event": "test event", "failure_type_override": "FooBar"},
        )
        assert response.status_code == 422

    def test_post_remediate_rejects_missing_raw_event(self):
        response = client.post("/remediate", json={})
        assert response.status_code == 422
