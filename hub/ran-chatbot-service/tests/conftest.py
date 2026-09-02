from unittest.mock import patch

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


@pytest.fixture()
def client():
    with patch("ran_chatbot_service.AnomaliesConsumer"):
        with TestClient(app) as test_client:
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
