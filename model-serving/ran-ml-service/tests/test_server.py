"""Tests for the ran-ml-service FastAPI server."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def client():
    with patch("ran_ml_service.model.predictor._loaded", True):
        from ran_ml_service.server import app

        with TestClient(app) as test_client:
            yield test_client


@pytest.fixture
def unloaded_client():
    with patch("ran_ml_service.model.predictor._loaded", False):
        from ran_ml_service.server import app

        with TestClient(app) as test_client:
            yield test_client


@pytest.fixture
def sample_kpi_window():
    fixture_path = FIXTURES_DIR / "antenna_failure.json"
    fixture = json.loads(fixture_path.read_text())
    return fixture["kpi_window"]


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "task" in response.json()


class TestReadyEndpoint:
    def test_ready_when_model_loaded(self, client):
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["ready"] is True

    def test_ready_returns_503_when_model_not_loaded(self, unloaded_client):
        response = unloaded_client.get("/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False
        assert "model not loaded" in response.json()["reason"]


class TestDetectEndpoint:
    @patch("ran_ml_service.model.predictor.predict")
    def test_detect_returns_prediction(self, mock_predict, client, sample_kpi_window):
        mock_predict.return_value = {"label": "anomalous", "confidence": 0.94, "class_index": 1}

        response = client.post("/v1/detect", json={"kpi_window": sample_kpi_window})

        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "anomalous"
        assert data["confidence"] == 0.94
        assert data["class_index"] == 1

    @patch("ran_ml_service.model.predictor.predict")
    def test_detect_normal_sample(self, mock_predict, client, sample_kpi_window):
        mock_predict.return_value = {"label": "normal", "confidence": 0.98, "class_index": 0}

        response = client.post("/v1/detect", json={"kpi_window": sample_kpi_window})

        assert response.status_code == 200
        assert response.json()["label"] == "normal"

    def test_detect_returns_503_when_model_not_loaded(self, unloaded_client, sample_kpi_window):
        response = unloaded_client.post("/v1/detect", json={"kpi_window": sample_kpi_window})
        assert response.status_code == 503

    def test_detect_rejects_wrong_window_size(self, client):
        response = client.post("/v1/detect", json={"kpi_window": [{"RSRP": 0}] * 10})
        assert response.status_code == 422

    @patch("ran_ml_service.model.predictor.predict")
    def test_detect_inference_error_returns_500(self, mock_predict, client, sample_kpi_window):
        mock_predict.side_effect = RuntimeError("CUDA OOM")

        response = client.post("/v1/detect", json={"kpi_window": sample_kpi_window})
        assert response.status_code == 500


class TestPreprocessing:
    def test_preprocess_produces_correct_shape(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        tensor = predictor.preprocess(sample_kpi_window)
        assert tensor.shape == (1, 128, 18)
        assert tensor.dtype.is_floating_point

    def test_preprocess_encodes_protocols(self):
        from ran_ml_service.model import predictor

        kpi_window = [
            {
                "RSRP": -85.0, "DL_BLER": 0.0, "DL_MCS": 10.0, "UL_BLER": 0.0,
                "UL_MCS": 5.0, "UL_NPRB": 20, "UL_SNR": 15.0, "TX_Bytes": 1000,
                "RX_Bytes": 2000, "Estimated_UL_Buffer": 0, "PRBs_DL_Current": 50.0,
                "PRBs_UL_Current": 30.0, "PRB_Utilization_DL": 0.5,
                "PRB_Utilization_UL": 0.3, "UL_Protocol": "TCP",
                "UL_NumberOfPackets": 100, "DL_Protocol": "UDP",
                "DL_NumberOfPackets": 200,
            }
        ] * 128

        tensor = predictor.preprocess(kpi_window)
        assert tensor[0, 0, 14].item() == 0.0  # TCP -> 0
        assert tensor[0, 0, 16].item() == 1.0  # UDP -> 1
