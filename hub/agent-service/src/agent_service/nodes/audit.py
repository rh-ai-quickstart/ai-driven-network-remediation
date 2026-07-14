import json
import time
from datetime import datetime, timezone
from typing import Any

from kafka import KafkaProducer
from loguru import logger

from agent_service.config import KAFKA_AUDIT_TOPIC, KAFKA_BOOTSTRAP

# Must match failure_type enum in contracts/incident-audit.schema.json (narrower than FailureType).
_AUDIT_FAILURE_TYPES = frozenset({"OOMKilled", "CrashLoopBackOff", "ConfigError", "NetworkTimeout", "Unknown"})


def _audit_failure_type(raw: str | None) -> str:
    if raw in _AUDIT_FAILURE_TYPES:
        return raw
    return "Unknown"


def _audit_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_audit_payload(state) -> dict[str, Any]:
    """Build an incident-audit record matching contracts/incident-audit.schema.json."""
    rca = state.root_cause_analysis
    log_event = state.log_event
    result = state.remediation_result

    failure_type = "Unknown"
    severity = "medium"
    ai_confidence = 0.0
    if rca is not None:
        failure_type = _audit_failure_type(rca.failure_type)
        severity = rca.estimated_severity
        ai_confidence = rca.confidence

    edge_site_id = log_event.edge_site_id if log_event else "unknown"

    if result is not None:
        remediation_action = result.action_taken or state.decision or "none"
        remediation_success = result.success
        aap_job_id = result.job_id or None
    else:
        remediation_action = state.decision or "none"
        remediation_success = False
        aap_job_id = None

    total_duration_ms = state.total_duration_ms
    if total_duration_ms <= 0 and state.incident_start_ms > 0:
        total_duration_ms = max(0.0, time.time() * 1000 - state.incident_start_ms)

    payload: dict[str, Any] = {
        "timestamp": _audit_timestamp(),
        "incident_id": state.incident_id,
        "failure_type": failure_type,
        "severity": severity,
        "edge_site_id": edge_site_id,
        "ai_confidence": ai_confidence,
        "remediation_action": remediation_action,
        "remediation_success": remediation_success,
        "total_duration_ms": total_duration_ms,
    }
    if state.servicenow_ticket:
        payload["servicenow_ticket"] = state.servicenow_ticket
    if aap_job_id:
        payload["aap_job_id"] = aap_job_id
    return payload


def publish_audit_record(
    payload: dict[str, Any],
    *,
    bootstrap_servers: str | None = None,
    topic: str | None = None,
) -> int:
    bootstrap = bootstrap_servers or KAFKA_BOOTSTRAP
    audit_topic = topic or KAFKA_AUDIT_TOPIC
    producer = KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    try:
        future = producer.send(audit_topic, value=payload)
        metadata = future.get(timeout=10)
        return int(metadata.offset)
    finally:
        producer.close(timeout=10)


def audit_node(state: dict) -> dict:
    payload = build_audit_payload(state)
    try:
        offset = publish_audit_record(payload)
        logger.info(
            "Audit record published incident_id={} decision={} topic={} offset={}",
            payload["incident_id"],
            state.decision,
            KAFKA_AUDIT_TOPIC,
            offset,
        )
    except Exception:
        logger.exception(
            "Failed to publish audit record incident_id={}",
            payload.get("incident_id"),
        )
    return {"total_duration_ms": payload["total_duration_ms"]}
