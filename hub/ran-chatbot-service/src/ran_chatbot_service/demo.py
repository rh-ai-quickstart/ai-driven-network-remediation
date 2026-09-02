"""Demo trigger: inject a TelecomTS fixture sample into the real pipeline.

Publishes a JSON TelecomTS sample to ran-combined-metrics (the real input topic
ran-anomaly-detector consumes). The fixture catalog provides reproducible,
checked-in lab-trace windows for demos.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from telco_oran.catalog import get_fixture, fixture_to_kpi_message, list_scenarios

from .config import DEMO_METRICS_TOPIC, KAFKA_BOOTSTRAP

logger = logging.getLogger(__name__)

DEFAULT_SCENARIO = "antenna_failure"


def build_demo_sample(scenario: str) -> tuple[str, dict[str, Any]]:
    """Build a JSON message from a fixture catalog scenario.

    Returns (json_blob, meta) where meta is {"scenario", "incident_id"} —
    the normalized scenario name plus the incident handle the operator should
    reference in chat.
    """
    normalized = scenario.strip().lower().replace("-", "_").replace(" ", "_")
    fixture = get_fixture(normalized)
    actual_scenario = fixture["scenario"]

    incident_id = str(uuid.uuid4())[:8]
    message = fixture_to_kpi_message(fixture, incident_id)
    json_blob = json.dumps(message)

    meta = {
        "scenario": actual_scenario,
        "incident_id": incident_id,
        "zone": fixture["zone"],
        "application": fixture["application"],
    }
    return json_blob, meta


def publish_demo_metrics(json_blob: str) -> int:
    """Publish a demo JSON sample to ran-combined-metrics. Returns the message offset."""
    from kafka import KafkaProducer

    producer = KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    try:
        future = producer.send(DEMO_METRICS_TOPIC, value=json_blob.encode("utf-8"))
        metadata = future.get(timeout=10)
    finally:
        producer.close(timeout=10)
    logger.info("Published demo RAN metrics to %s at offset %d", DEMO_METRICS_TOPIC, metadata.offset)
    return int(metadata.offset)
