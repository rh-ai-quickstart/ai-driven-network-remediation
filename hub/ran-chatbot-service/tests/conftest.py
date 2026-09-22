from collections import deque
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from ran_chatbot_service import app
from ran_chatbot_service.models import EnrichedAnomaly

SAMPLE_ANOMALY_DICT = {
    "incident_id": "test-001",
    "zone": "A",
    "application": "Twitch",
    "kpi_window": [{"RSRP": -85.0, "DL_BLER": 0.1}] * 128,
    "ad_label": "anomalous",
    "ad_confidence": 0.94,
    "root_cause": "Signal degradation due to antenna misalignment in zone A.",
    "recommended_fix": "Section 4.2 — Verify antenna tilt and azimuth alignment.",
}

SAMPLE_REMEDIATION_DICT = {
    "incident_id": "test-001",
    "zone": "A",
    "application": "Twitch",
    "ad_label": "anomalous",
    "ad_confidence": 0.94,
    "root_cause": "Signal degradation due to antenna misalignment in zone A.",
    "template_name": "ran-antenna-tilt-adjust",
    "job_id": "42",
    "job_status": "successful",
    "success": True,
    "timed_out": False,
    "output_summary": "PLAY RECAP: ok=3 changed=2",
    "timestamp": "2026-09-22T10:00:00Z",
    "total_duration_ms": 1500.0,
}


@pytest.fixture()
def client():
    mock_anomalies_consumer = MagicMock()
    mock_anomalies_consumer.is_connected = True
    mock_remediation_consumer = MagicMock()
    mock_remediation_consumer.is_connected = True

    with patch("ran_chatbot_service.AnomaliesConsumer", return_value=mock_anomalies_consumer), \
         patch("ran_chatbot_service.RemediationConsumer", return_value=mock_remediation_consumer):
        with TestClient(app) as test_client:
            test_client.app.state.recent_remediations = deque(maxlen=100)
            yield test_client


@pytest.fixture()
def sample_anomaly_dict() -> dict:
    """The JSON-serializable form, for building fake Kafka message payloads."""
    return dict(SAMPLE_ANOMALY_DICT)


@pytest.fixture()
def sample_anomaly(sample_anomaly_dict) -> EnrichedAnomaly:
    return EnrichedAnomaly(**sample_anomaly_dict)


@pytest.fixture()
def sample_anomalies(sample_anomaly) -> list[EnrichedAnomaly]:
    return [sample_anomaly]


@pytest.fixture()
def sample_remediation() -> dict:
    return dict(SAMPLE_REMEDIATION_DICT)
