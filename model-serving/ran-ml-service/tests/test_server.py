"""Tests for the ran-ml-service FastAPI server."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ran_ml_service.model import InvalidKpiWindowError

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def client():
    with (
        patch("ran_ml_service.model.predictor._loaded", True),
        patch("ran_ml_service.model.predictor.load"),
    ):
        from ran_ml_service.server import app

        with TestClient(app) as test_client:
            yield test_client


@pytest.fixture
def unloaded_client():
    with (
        patch("ran_ml_service.model.predictor._loaded", False),
        patch("ran_ml_service.model.predictor.load"),
    ):
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
    def test_invalid_model_keeps_health_live_but_readiness_false(self):
        from ran_ml_service.server import app

        with (
            patch("ran_ml_service.server.predictor.load", side_effect=ValueError("wrong task")),
            patch("ran_ml_service.server.predictor._loaded", False),
            TestClient(app) as client,
        ):
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 503

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


class TestClassifyEndpoint:
    def test_classify_returns_503_when_model_not_loaded(self, unloaded_client, sample_kpi_window):
        with patch("ran_ml_service.server.TASK", "classify"):
            response = unloaded_client.post("/v1/classify", json={"kpi_window": sample_kpi_window})
        assert response.status_code == 503

    def test_task_mismatch_rejects_checkpoint_before_backbone_load(self):
        from ran_ml_service.model import MantisPredictor

        candidate = MantisPredictor()
        with (
            patch.object(candidate, "_resolve_weights_path", return_value="/tmp/rca.pt"),
            patch("ran_ml_service.model.torch.load", return_value={
                "task": "root-cause analysis", "n_classes": 10,
                "checkpoint": "paris-noah/Mantis-8M",
            }),
        ):
            with pytest.raises(ValueError, match="do not match TASK=detect"):
                candidate.load()
        assert not candidate.is_ready

    @patch("ran_ml_service.model.predictor.predict")
    def test_classify_returns_class_index_and_confidence(self, mock_predict, client, sample_kpi_window):
        mock_predict.return_value = {"label": "Antenna Failure", "confidence": 0.91, "class_index": 0}
        with patch("ran_ml_service.server.TASK", "classify"):
            response = client.post("/v1/classify", json={"kpi_window": sample_kpi_window})
        assert response.status_code == 200
        assert response.json() == {"class": "Antenna Failure", "confidence": 0.91, "class_index": 0}

    def test_classify_disabled_in_detect_mode(self, client, sample_kpi_window):
        assert client.post("/v1/classify", json={"kpi_window": sample_kpi_window}).status_code == 404

    def test_rca_preprocess_uses_fixed_train_statistics(self, sample_kpi_window):
        from ran_ml_service.model import MantisPredictor
        import torch

        candidate = MantisPredictor()
        candidate.task = "root-cause analysis"
        candidate.rca_means = torch.zeros((1, 1, 18))
        candidate.rca_stds = torch.full((1, 1, 18), 2.0)
        x = candidate.preprocess(sample_kpi_window)
        assert x.shape == (1, 128, 18)
        assert x[0, 0, 0].item() == pytest.approx(sample_kpi_window[0]["RSRP"] / 2)


class TestDetectValidation:
    def test_detect_rejects_wrong_window_size(self, client):
        response = client.post("/v1/detect", json={"kpi_window": [{"RSRP": 0}] * 10})
        assert response.status_code == 422

    @patch("ran_ml_service.model.predictor.predict")
    def test_detect_inference_error_returns_500(self, mock_predict, client, sample_kpi_window):
        mock_predict.side_effect = RuntimeError("CUDA OOM")

        response = client.post("/v1/detect", json={"kpi_window": sample_kpi_window})
        assert response.status_code == 500

    def test_detect_rejects_missing_kpi_field(self, client, sample_kpi_window):
        kpi_window = copy.deepcopy(sample_kpi_window)
        del kpi_window[0]["RSRP"]

        response = client.post("/v1/detect", json={"kpi_window": kpi_window})

        assert response.status_code == 422
        assert "missing KPI 'RSRP'" in response.json()["error"]

    def test_detect_rejects_null_numeric_kpi(self, client, sample_kpi_window):
        kpi_window = copy.deepcopy(sample_kpi_window)
        kpi_window[3]["DL_BLER"] = None

        response = client.post("/v1/detect", json={"kpi_window": kpi_window})

        assert response.status_code == 422
        assert "null KPI 'DL_BLER'" in response.json()["error"]


class TestPreprocessing:
    def test_fixed_rca_statistics_match_channel_contract(self):
        from ran_ml_service.model import KPI_CHANNELS

        artifact = json.loads((Path(__file__).resolve().parents[1] / "artifacts/rca_preprocessing.json").read_text())
        assert artifact["channels"] == KPI_CHANNELS
        assert artifact["dataset_revision"] == "1b3a88a440f4922edfcd1e230e42c003c0e13bdf"
        assert artifact["train"]["examples"] > 0
        assert len(artifact["train"]["means"]) == len(artifact["train"]["stds"]) == 18

    def test_preprocess_produces_correct_shape(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        tensor = predictor.preprocess(sample_kpi_window)
        assert tensor.shape == (1, 128, 18)
        assert tensor.dtype.is_floating_point

    def test_preprocess_encodes_protocols(self):
        from ran_ml_service.model import predictor

        kpi_window = [
            {
                "RSRP": -85.0,
                "DL_BLER": 0.0,
                "DL_MCS": 10.0,
                "UL_BLER": 0.0,
                "UL_MCS": 5.0,
                "UL_NPRB": 20,
                "UL_SNR": 15.0,
                "TX_Bytes": 1000,
                "RX_Bytes": 2000,
                "Estimated_UL_Buffer": 0,
                "PRBs_DL_Current": 50.0,
                "PRBs_UL_Current": 30.0,
                "PRB_Utilization_DL": 0.5,
                "PRB_Utilization_UL": 0.3,
                "UL_Protocol": "TCP",
                "UL_NumberOfPackets": 100,
                "DL_Protocol": "UDP",
                "DL_NumberOfPackets": 200,
            }
        ] * 128

        tensor = predictor.preprocess(kpi_window)
        assert tensor[0, 0, 14].item() == 0.0  # TCP -> 0
        assert tensor[0, 0, 16].item() == 1.0  # UDP -> 1

    def test_preprocess_encodes_null_protocol_as_none_class(self):
        from ran_ml_service.model import predictor

        kpi_window = [
            {
                "RSRP": -85.0,
                "DL_BLER": 0.0,
                "DL_MCS": 10.0,
                "UL_BLER": 0.0,
                "UL_MCS": 5.0,
                "UL_NPRB": 20,
                "UL_SNR": 15.0,
                "TX_Bytes": 1000,
                "RX_Bytes": 2000,
                "Estimated_UL_Buffer": 0,
                "PRBs_DL_Current": 50.0,
                "PRBs_UL_Current": 30.0,
                "PRB_Utilization_DL": 0.5,
                "PRB_Utilization_UL": 0.3,
                "UL_Protocol": None,
                "UL_NumberOfPackets": 100,
                "DL_Protocol": "None",
                "DL_NumberOfPackets": 200,
            }
        ] * 128

        tensor = predictor.preprocess(kpi_window)
        assert tensor[0, 0, 14].item() == 2.0  # None -> 2
        assert tensor[0, 0, 16].item() == 2.0  # "None" -> 2

    def test_preprocess_keeps_explicit_zero(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        kpi_window = copy.deepcopy(sample_kpi_window)
        kpi_window[0]["DL_BLER"] = 0.0

        tensor = predictor.preprocess(kpi_window)
        assert tensor[0, 0, 1].item() == 0.0

    def test_preprocess_rejects_missing_kpi_field(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        kpi_window = copy.deepcopy(sample_kpi_window)
        del kpi_window[0]["RSRP"]

        with pytest.raises(InvalidKpiWindowError, match="missing KPI 'RSRP' at timestep 0"):
            predictor.preprocess(kpi_window)

    def test_preprocess_rejects_null_numeric_kpi(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        kpi_window = copy.deepcopy(sample_kpi_window)
        kpi_window[5]["UL_SNR"] = None

        with pytest.raises(InvalidKpiWindowError, match="null KPI 'UL_SNR' at timestep 5"):
            predictor.preprocess(kpi_window)

    def test_preprocess_rejects_invalid_protocol(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        kpi_window = copy.deepcopy(sample_kpi_window)
        kpi_window[0]["UL_Protocol"] = "SCTP"

        with pytest.raises(InvalidKpiWindowError, match="invalid UL_Protocol 'SCTP'"):
            predictor.preprocess(kpi_window)

    def test_preprocess_rejects_non_numeric_kpi(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        kpi_window = copy.deepcopy(sample_kpi_window)
        kpi_window[1]["RSRP"] = "down"

        with pytest.raises(InvalidKpiWindowError, match="non-numeric KPI 'RSRP'"):
            predictor.preprocess(kpi_window)

    def test_preprocess_rejects_nan_kpi(self, sample_kpi_window):
        from ran_ml_service.model import predictor

        kpi_window = copy.deepcopy(sample_kpi_window)
        kpi_window[0]["TX_Bytes"] = float("nan")

        with pytest.raises(InvalidKpiWindowError, match="NaN KPI 'TX_Bytes'"):
            predictor.preprocess(kpi_window)
