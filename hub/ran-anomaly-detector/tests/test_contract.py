"""JSON Schema validation tests for the ran-anomalies contract.

Ensures detector output matches contracts/ran-anomalies.schema.json.
"""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import jsonschema
import pytest
from conftest import synthetic_fixture_message
from ran_anomaly_detector.detection import AnomalyDetectionService

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


@pytest.fixture
def schema():
    path = CONTRACTS_DIR / "ran-anomalies.schema.json"
    return json.loads(path.read_text())


@pytest.fixture
def validator(schema):
    return jsonschema.Draft202012Validator(schema)


class TestRanAnomaliesContract:
    @patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", "http://predictor:8080/v1/detect")
    def test_anomalous_output_matches_schema(self, validator):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("antenna_failure", incident_id="schema-test-001")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = httpx.Response(
                200, request=httpx.Request("POST", "http://predictor:8080/v1/detect"), json={"label": "anomalous", "confidence": 0.94, "class_index": 1}
            )
            outputs = service.process_message(msg)

        assert len(outputs) == 1
        validator.validate(outputs[0])

    @patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", "http://predictor:8080/v1/detect")
    def test_output_has_all_required_fields(self, validator):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("co_channel_interference_severe", incident_id="schema-test-001")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = httpx.Response(
                200, request=httpx.Request("POST", "http://predictor:8080/v1/detect"), json={"label": "anomalous", "confidence": 0.87, "class_index": 1}
            )
            outputs = service.process_message(msg)

        output = outputs[0]
        assert output["incident_id"] == "schema-test-001"
        assert output["zone"] == "A"
        assert output["ad_label"] == "anomalous"
        assert output["ad_confidence"] == 0.87
        assert len(output["kpi_window"]) == 128

    @patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", "http://predictor:8080/v1/detect")
    def test_normal_produces_no_output_no_schema_violation(self, validator):
        """Normal samples produce no output — nothing to validate, but verify the
        absence is deliberate (the contract says ad_label can only be 'anomalous')."""
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("normal_traffic")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = httpx.Response(
                200, request=httpx.Request("POST", "http://predictor:8080/v1/detect"), json={"label": "normal", "confidence": 0.99, "class_index": 0}
            )
            outputs = service.process_message(msg)

        assert outputs == []

    @patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", "http://predictor:8080/v1/detect")
    def test_output_kpi_window_has_128_timesteps(self, validator):
        service = AnomalyDetectionService()
        msg = synthetic_fixture_message("doppler_shift_severe")

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_client:
            mock_client.return_value.post.return_value = httpx.Response(
                200, request=httpx.Request("POST", "http://predictor:8080/v1/detect"), json={"label": "anomalous", "confidence": 0.91, "class_index": 1}
            )
            outputs = service.process_message(msg)

        output = outputs[0]
        assert len(output["kpi_window"]) == 128
        first_timestep = output["kpi_window"][0]
        assert "RSRP" in first_timestep
        assert "DL_BLER" in first_timestep
