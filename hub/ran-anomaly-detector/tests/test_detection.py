"""Tests for the ML-based anomaly detection service.

Primary test seam: JSON sample in -> (mocked HTTP detect) -> ran-anomalies output dict.
"""

import json
from unittest.mock import patch

import httpx
import pytest
from conftest import synthetic_fixture, synthetic_fixture_message
from ran_anomaly_detector.detection import AnomalyDetectionService

pytestmark = pytest.mark.usefixtures("_patch_detect_url")


@pytest.fixture(autouse=True)
def _patch_detect_url():
    with (
        patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", "http://predictor:8080/v1/detect"),
        patch("ran_anomaly_detector.detection.DETECT_TOKEN", ""),
    ):
        yield


_DETECT_REQUEST = httpx.Request("POST", "http://predictor:8080/v1/detect")


def _mock_detect_response(label: str, confidence: float):
    """Create a mock httpx.Response for the detect predictor."""
    response = httpx.Response(
        200,
        json={"label": label, "confidence": confidence, "class_index": 1 if label == "anomalous" else 0},
        request=_DETECT_REQUEST,
    )
    return response


class TestAnomalyDetectionService:
    def test_inference_sends_bearer_token_when_configured(self):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("antenna_failure")

        with (
            patch("ran_anomaly_detector.detection.DETECT_TOKEN", "detect-token"),
            patch("ran_anomaly_detector.detection._get_http_client") as mock_client,
        ):
            mock_client.return_value.post.return_value = _mock_detect_response("anomalous", 0.94)
            service.process_message(msg)

        assert mock_client.return_value.post.call_args.kwargs["headers"] == {"Authorization": "Bearer detect-token"}

    def test_inference_omits_authorization_when_token_is_empty(self):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("antenna_failure")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("anomalous", 0.94)
            service.process_message(msg)

        assert "headers" not in mock_client.return_value.post.call_args.kwargs

    def test_anomalous_sample_produces_output(self):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("antenna_failure")

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
        msg = synthetic_fixture_message("normal_traffic")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("normal", 0.98)
            outputs = service.process_message(msg)

        assert outputs == []

    def test_detect_http_failure_propagates(self):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("antenna_failure")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = httpx.Response(500, text="Internal Server Error", request=_DETECT_REQUEST)
            with pytest.raises(httpx.HTTPStatusError):
                service.process_message(msg)

    def test_detect_timeout_propagates(self):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("antenna_failure")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.side_effect = httpx.TimeoutException("timed out")
            with pytest.raises(httpx.TimeoutException):
                service.process_message(msg)

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
        msg = synthetic_fixture_message("co_channel_interference_severe")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("anomalous", 0.87)
            outputs = service.process_message(msg)

        assert len(outputs) == 1
        output = outputs[0]
        required_keys = {"incident_id", "zone", "application", "kpi_window", "ad_label", "ad_confidence"}
        assert required_keys.issubset(output.keys())
        assert output["ad_label"] == "anomalous"
        assert 0 <= output["ad_confidence"] <= 1

    def test_generates_incident_id_if_not_in_message(self):
        service = AnomalyDetectionService()
        fixture = synthetic_fixture("antenna_failure")
        msg = json.dumps(
            {
                "zone": fixture["zone"],
                "application": fixture["application"],
                "kpi_window": fixture["kpi_window"],
            }
        ).encode()

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = _mock_detect_response("anomalous", 0.91)
            outputs = service.process_message(msg)

        assert len(outputs) == 1
        assert outputs[0]["incident_id"] != ""
