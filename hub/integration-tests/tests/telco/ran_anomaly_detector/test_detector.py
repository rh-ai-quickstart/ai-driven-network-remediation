"""Integration tests for the ran-anomaly-detector service.

These run against a deployed ran-anomaly-detector (via port-forward or direct URL).
Set RAN_ANOMALY_DETECTOR_URL env var to override the default http://localhost:8002.

Validates:
- Health and readiness (including predictor dependency)
- Anomalies buffer endpoint
"""

import pytest

pytestmark = pytest.mark.telco


class TestHealth:
    def test_health_returns_ok(self, ran_anomaly_detector_client):
        response = ran_anomaly_detector_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_checks_kafka_and_predictor(self, ran_anomaly_detector_client):
        response = ran_anomaly_detector_client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True


class TestAnomaliesBuffer:
    def test_anomalies_returns_list(self, ran_anomaly_detector_client):
        response = ran_anomaly_detector_client.get("/anomalies")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "anomalies" in data
        assert isinstance(data["anomalies"], list)

    def test_anomalies_respect_new_schema(self, ran_anomaly_detector_client):
        response = ran_anomaly_detector_client.get("/anomalies")
        data = response.json()
        for anomaly in data["anomalies"]:
            assert "incident_id" in anomaly
            assert "zone" in anomaly
            assert "application" in anomaly
            assert "ad_label" in anomaly
            assert "ad_confidence" in anomaly
            assert "kpi_window" in anomaly
            assert anomaly["ad_label"] == "anomalous"
            assert 0 <= anomaly["ad_confidence"] <= 1
            assert "cell_id" not in anomaly
            assert "band" not in anomaly
