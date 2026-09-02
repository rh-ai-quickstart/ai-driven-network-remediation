"""Tests for the ML-based anomaly detection service.

Primary test seam: JSON sample in -> (mocked HTTP detect) -> ran-anomalies output dict.
"""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from ran_anomaly_detector.detection import AnomalyDetectionService

pytestmark = pytest.mark.usefixtures("_patch_detect_url")


@pytest.fixture(autouse=True)
def _patch_detect_url():
    with patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", "http://predictor:8080/v1/detect"):
        yield

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "telco-oran" / "src" / "telco_oran" / "fixtures"


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


def _fixture_message(name: str) -> bytes:
    """Build a Kafka message payload from a fixture (same format demo trigger publishes)."""
    fixture = _load_fixture(name)
    msg = {
        "incident_id": "test-001",
        "zone": fixture["zone"],
        "application": fixture["application"],
        "kpi_window": fixture["kpi_window"],
    }
    return json.dumps(msg).encode("utf-8")


def _mock_detect_response(label: str, confidence: float):
    """Create a mock httpx.Response for the detect predictor."""
    response = httpx.Response(
        200,
        json={"label": label, "confidence": confidence, "class_index": 1 if label == "anomalous" else 0},
    )
    return response


class TestAnomalyDetectionService:
    def test_anomalous_sample_produces_output(self):
        service = AnomalyDetectionService()
        msg = _fixture_message("antenna_failure")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("anomalous", 0.94)
            outputs = service.process_message(msg)

        assert len(outputs) == 1
        output = outputs[0]
        assert output["incident_id"] == "test-001"
        assert output["zone"] == "A"
        assert output["ad_label"] == "anomalous"
        assert output["ad_confidence"] == 0.94
        assert len(output["kpi_window"]) == 128
        assert "application" in output

    def test_normal_sample_produces_no_output(self):
        service = AnomalyDetectionService()
        msg = _fixture_message("normal_traffic")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("normal", 0.98)
            outputs = service.process_message(msg)

        assert outputs == []

    def test_detect_http_failure_produces_no_output(self):
        service = AnomalyDetectionService()
        msg = _fixture_message("antenna_failure")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = httpx.Response(500, text="Internal Server Error")
            outputs = service.process_message(msg)

        assert outputs == []

    def test_detect_timeout_produces_no_output(self):
        service = AnomalyDetectionService()
        msg = _fixture_message("antenna_failure")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.side_effect = httpx.TimeoutException("timed out")
            outputs = service.process_message(msg)

        assert outputs == []

    def test_empty_message_returns_no_output(self):
        service = AnomalyDetectionService()
        assert service.process_message(b"") == []
        assert service.process_message(b"   ") == []

    def test_non_json_message_returns_no_output(self):
        service = AnomalyDetectionService()
        assert service.process_message(b"not json at all") == []

    def test_missing_kpi_window_returns_no_output(self):
        service = AnomalyDetectionService()
        msg = json.dumps({"incident_id": "x", "zone": "A"}).encode()
        assert service.process_message(msg) == []

    def test_output_matches_ran_anomalies_schema_shape(self):
        service = AnomalyDetectionService()
        msg = _fixture_message("co_channel_interference_severe")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("anomalous", 0.87)
            outputs = service.process_message(msg)

        assert len(outputs) == 1
        output = outputs[0]
        required_keys = {"incident_id", "zone", "application", "kpi_window", "ad_label", "ad_confidence"}
        assert required_keys.issubset(output.keys())
        assert output["ad_label"] == "anomalous"
        assert 0 <= output["ad_confidence"] <= 1

    def test_no_inference_url_returns_no_output(self):
        with patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", ""):
            service = AnomalyDetectionService()
            msg = _fixture_message("antenna_failure")
            outputs = service.process_message(msg)
            assert outputs == []

    def test_generates_incident_id_if_not_in_message(self):
        service = AnomalyDetectionService()
        fixture = _load_fixture("antenna_failure")
        msg = json.dumps({
            "zone": fixture["zone"],
            "application": fixture["application"],
            "kpi_window": fixture["kpi_window"],
        }).encode()

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("anomalous", 0.91)
            outputs = service.process_message(msg)

        assert len(outputs) == 1
        assert outputs[0]["incident_id"] != ""
