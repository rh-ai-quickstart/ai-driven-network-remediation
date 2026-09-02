"""Integration tests for the ran-ml-service ML predictor.

These run against a deployed ran-ml-service (via port-forward or direct URL).
Set RAN_ML_SERVICE_URL env var to override the default http://localhost:8080.

Validates:
- Model loading and readiness
- Binary anomaly detection on TelecomTS fixtures
- Correct label and confidence ranges
- Error handling for malformed input
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.telco

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "telco-oran" / "src" / "telco_oran" / "fixtures"


def _load_fixture_kpi_window(name: str) -> list[dict]:
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"Fixture {name}.json not found at {FIXTURES_DIR}")
    fixture = json.loads(path.read_text())
    return fixture["kpi_window"]


class TestHealth:
    def test_health_returns_ok(self, ran_ml_service_client):
        response = ran_ml_service_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["task"] == "detect"

    def test_ready_returns_model_loaded(self, ran_ml_service_client):
        response = ran_ml_service_client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["task"] == "detect"


class TestDetectAnomalous:
    def test_antenna_failure_detected_as_anomalous(self, ran_ml_service_client):
        kpi_window = _load_fixture_kpi_window("antenna_failure")
        response = ran_ml_service_client.post("/v1/detect", json={"kpi_window": kpi_window})

        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "anomalous"
        assert data["confidence"] > 0.8
        assert data["class_index"] == 1

    def test_high_congestion_detected_as_anomalous(self, ran_ml_service_client):
        kpi_window = _load_fixture_kpi_window("high_congestion_sudden")
        response = ran_ml_service_client.post("/v1/detect", json={"kpi_window": kpi_window})

        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "anomalous"
        assert data["confidence"] > 0.7

    def test_co_channel_interference_detected_as_anomalous(self, ran_ml_service_client):
        kpi_window = _load_fixture_kpi_window("co_channel_interference_severe")
        response = ran_ml_service_client.post("/v1/detect", json={"kpi_window": kpi_window})

        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "anomalous"
        assert data["confidence"] > 0.7

    def test_doppler_shift_detected_as_anomalous(self, ran_ml_service_client):
        kpi_window = _load_fixture_kpi_window("doppler_shift_severe")
        response = ran_ml_service_client.post("/v1/detect", json={"kpi_window": kpi_window})

        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "anomalous"
        assert data["confidence"] > 0.7


class TestDetectNormal:
    def test_normal_traffic_classified_as_normal(self, ran_ml_service_client):
        kpi_window = _load_fixture_kpi_window("normal_traffic")
        response = ran_ml_service_client.post("/v1/detect", json={"kpi_window": kpi_window})

        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "normal"
        assert data["confidence"] > 0.8
        assert data["class_index"] == 0


class TestDetectErrorHandling:
    def test_wrong_window_size_returns_422(self, ran_ml_service_client):
        response = ran_ml_service_client.post("/v1/detect", json={"kpi_window": [{"RSRP": 0}] * 10})
        assert response.status_code == 422

    def test_missing_kpi_window_returns_422(self, ran_ml_service_client):
        response = ran_ml_service_client.post("/v1/detect", json={})
        assert response.status_code == 422

    def test_response_has_expected_schema(self, ran_ml_service_client):
        kpi_window = _load_fixture_kpi_window("antenna_failure")
        response = ran_ml_service_client.post("/v1/detect", json={"kpi_window": kpi_window})

        data = response.json()
        assert "label" in data
        assert "confidence" in data
        assert "class_index" in data
        assert data["label"] in ("anomalous", "normal")
        assert 0.0 <= data["confidence"] <= 1.0
        assert data["class_index"] in (0, 1)
