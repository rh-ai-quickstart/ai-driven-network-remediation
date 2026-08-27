from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ran_chatbot_service import app
from ran_chatbot_service.models import EnrichedAnomaly

SAMPLE_ANOMALY_DICT = {
    "cell_id": 42,
    "band": "Band 29",
    "anomaly_type": "LowRsrp",
    "anomaly": "Low RSRP: -125.0 dBm < -110.0 dBm",
    "root_cause": "Poor radio conditions.",
    "recommended_fix": "Section 4.2 — Antenna Tilt Adjustment",
    "ml_root_cause_class": "",
    "ml_confidence": 0.0,
    "ml_steer_used": False,
}


@pytest.fixture()
def client():
    # Patch out the real background Kafka consumer so the lifespan startup event
    # doesn't try to connect to a real broker; using TestClient as a context
    # manager triggers that lifespan (startup populates app.state.recent_anomalies
    # / app.state.kafka_consumer, shutdown calls consumer.stop()).
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
