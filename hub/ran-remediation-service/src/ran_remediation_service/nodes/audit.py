"""Audit node — publishes a remediation record to ran-remediation-results Kafka topic.

Same pattern as agent-service nodes/audit.py (Workflow 1): builds a structured
payload and publishes it via KafkaProducer. Mirrors the incident-audit pattern
but with ML-schema RAN fields.
"""

from __future__ import annotations

import json
import time

from kafka import KafkaProducer
from loguru import logger

from ran_remediation_service.config import (
    KAFKA_BOOTSTRAP,
    KAFKA_REMEDIATION_TOPIC,
    now_iso,
)
from ran_remediation_service.models import RemediationState


def build_audit_payload(state: RemediationState) -> dict:
    total_ms = state.total_duration_ms
    if total_ms <= 0 and state.incident_start_ms > 0:
        total_ms = max(0.0, time.time() * 1000 - state.incident_start_ms)

    return {
        "timestamp":         state.timestamp or now_iso(),
        "incident_id":       state.incident_id,
        "zone":              state.zone,
        "application":       state.application,
        "ad_label":          state.ad_label,
        "ad_confidence":     state.ad_confidence,
        "root_cause":        state.root_cause,
        "template_name":     state.template_name,
        "job_id":            state.job_id,
        "job_status":        state.job_status,
        "success":           state.success,
        "timed_out":         state.timed_out,
        "output_summary":    state.output_summary,
        "total_duration_ms": total_ms,
    }


def publish_remediation_record(
    payload: dict,
    *,
    bootstrap_servers: str | None = None,
    topic: str | None = None,
) -> int:
    bootstrap = bootstrap_servers or KAFKA_BOOTSTRAP
    remediation_topic = topic or KAFKA_REMEDIATION_TOPIC
    producer = KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    try:
        future = producer.send(remediation_topic, value=payload)
        metadata = future.get(timeout=10)
        return int(metadata.offset)
    finally:
        producer.close(timeout=10)


def audit_node(state: RemediationState) -> dict:
    payload = build_audit_payload(state)
    try:
        offset = publish_remediation_record(payload)
        logger.info(
            "Remediation audit published incident_id={} success={} topic={} offset={}",
            state.incident_id,
            state.success,
            KAFKA_REMEDIATION_TOPIC,
            offset,
        )
    except Exception:
        logger.exception(
            "Failed to publish remediation audit record incident_id={}",
            state.incident_id,
        )
    return {"total_duration_ms": payload["total_duration_ms"]}
