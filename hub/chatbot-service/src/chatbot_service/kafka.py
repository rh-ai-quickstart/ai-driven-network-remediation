"""Kafka operations: audit consumer and demo event producer."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .config import (
    AUDIT_LOOKBACK_HOURS,
    AUDIT_MAX_MESSAGES,
    AUDIT_TOPIC,
    DEMO_TOPIC,
    KAFKA_BOOTSTRAP,
)
from .slo import normalize_incident_record
from .utils import parse_iso

logger = logging.getLogger(__name__)


def fetch_recent_audits() -> tuple[list[dict[str, Any]], bool]:
    """Read recent incident-audit records from Kafka using seek-to-end.

    Returns (records, kafka_reachable).
    """
    from kafka import KafkaConsumer

    records: list[dict[str, Any]] = []
    try:
        consumer = KafkaConsumer(
            AUDIT_TOPIC,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            auto_offset_reset="latest",
            enable_auto_commit=False,
            consumer_timeout_ms=2500,
            request_timeout_ms=10000,
            value_deserializer=lambda m: m.decode("utf-8", errors="replace"),
        )
    except Exception:
        logger.warning("Failed to connect to Kafka at %s", KAFKA_BOOTSTRAP, exc_info=True)
        return records, False

    try:
        consumer.poll(timeout_ms=800)
        partitions = consumer.assignment()
        if not partitions:
            logger.debug("No partitions assigned for topic %s", AUDIT_TOPIC)
            return records, True

        max_per_partition = max(50, AUDIT_MAX_MESSAGES // max(1, len(partitions)))
        for tp in partitions:
            end_offset = consumer.end_offsets([tp])[tp]
            start_offset = max(0, end_offset - max_per_partition)
            consumer.seek(tp, start_offset)

        cutoff = datetime.now(timezone.utc).timestamp() - (AUDIT_LOOKBACK_HOURS * 3600)
        for msg in consumer:
            try:
                data = json.loads(msg.value)
            except Exception:
                logger.debug("Skipping non-JSON message at offset %s", msg.offset)
                continue
            normalized = normalize_incident_record(data)
            ts = parse_iso(normalized.get("timestamp"))
            if ts and ts.timestamp() >= cutoff:
                records.append(normalized)
            elif not ts:
                records.append(normalized)
            if len(records) >= AUDIT_MAX_MESSAGES:
                break
    except Exception:
        logger.exception("Error consuming from Kafka topic %s", AUDIT_TOPIC)
        return records, False
    finally:
        consumer.close()
    logger.info("Fetched %d audit records from %s", len(records), AUDIT_TOPIC)
    return records, True


def build_demo_event(scenario: str, site: str, incident_id: str) -> dict[str, Any]:
    """Build a structured log event for a demo failure scenario."""
    now = datetime.now(timezone.utc).isoformat()
    normalized = scenario.strip().lower()

    scenarios = {
        "oom": ("OOMKilled: container exceeded memory limits", "nginx-edge-oom"),
        "lightspeed": ("Ansible playbook generation requested for edge recovery", "nginx-edge-lightspeed"),
        "escalation": ("Kernel panic and persistent data corruption - requires human escalation", "edge-core-critical"),
    }
    message, pod_name = scenarios.get(normalized, (
        "CrashLoopBackOff: nginx configuration test failed",
        "nginx-edge-crashloop",
    ))
    if normalized not in scenarios:
        normalized = "crashloop"

    return {
        "@timestamp": now,
        "incident_id": incident_id,
        "message": message,
        "level": "error",
        "kubernetes": {
            "namespace_name": "dark-noc-edge",
            "pod_name": pod_name,
            "container_name": "nginx",
        },
        "labels": {
            "edge_site_id": site,
            "dark_noc_demo": "true",
            "dark_noc_scenario": normalized,
        },
    }


def publish_demo_event(event: dict[str, Any]) -> int:
    """Publish a demo event to Kafka. Returns the message offset."""
    from kafka import KafkaProducer

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    future = producer.send(DEMO_TOPIC, value=event)
    metadata = future.get(timeout=10)
    producer.flush(timeout=10)
    producer.close(timeout=10)
    logger.info("Published demo event to %s at offset %d", DEMO_TOPIC, metadata.offset)
    return int(metadata.offset)
