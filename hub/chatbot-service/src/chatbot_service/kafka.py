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
    CPU_DEMO_SITE_A,
    CPU_DEMO_SITE_B,
    DEMO_TOPIC,
    DEMO_WORKLOAD,
    EDGE_NAMESPACE,
    KAFKA_BOOTSTRAP,
)
from .slo import normalize_incident_record
from .utils import parse_iso

logger = logging.getLogger(__name__)

CPU_DEMO_SCENARIO = "cpu-misconfiguration"
CPU_DEMO_SOURCE_WORKLOAD = "microservice-a"
CPU_DEMO_TARGET_WORKLOAD = "microservice-b"
CPU_DEMO_TARGET_CPU_LIMIT = "250m"


def _cpu_misconfiguration_message(site_a: str, site_b: str) -> str:
    if site_a == site_b:
        topology_hint = f"Both services run on the same cluster ({site_a})."
        loki_hint = "Workload logs are unavailable from LokiStack for this demo."
        pods_hint = f"Call get_pods for namespace {EDGE_NAMESPACE} on {site_a} to compare both workloads' placement and health."
        logs_hint = f"Use get_pod_logs with edge_site_id={site_a} for logs from each service."
    else:
        topology_hint = f"microservice-b is on a different cluster ({site_b}), not on {site_a} where microservice-a runs."
        loki_hint = "Spoke workload logs are not forwarded to the hub LokiStack, so the query is denied and returns nothing."
        pods_hint = f"Call get_pods for namespace {EDGE_NAMESPACE} on both {site_a} and {site_b} to compare placement and health."
        logs_hint = f"Use get_pod_logs with edge_site_id={site_a} for microservice-a and edge_site_id={site_b} for microservice-b."

    return (
        f"{CPU_DEMO_SOURCE_WORKLOAD} request queue is full (depth rising, tasks not draining). "
        "Its deployment looks healthy: CPU and memory within limits, no restarts, replicas ready. "
        f"Downstream {CPU_DEMO_TARGET_WORKLOAD} latency is degraded, so the backpressure originates there. "
        f"{topology_hint} Do NOT use LokiStack search_logs for namespace {EDGE_NAMESPACE}: {loki_hint} "
        f"Gather evidence with the OpenShift tools. {pods_hint} {logs_hint} "
        f"On {site_b}, call get_pod_spec for the {CPU_DEMO_TARGET_WORKLOAD} pod and use its resource limits as evidence; "
        f"you MUST pass edge_site_id={site_b} and namespace={EDGE_NAMESPACE} on that call. "
        f"The {CPU_DEMO_TARGET_WORKLOAD} is CPU-throttled due to an insufficient CPU limit. "
        f"Generate a minimal remediation playbook whose only mutation is increasing the {CPU_DEMO_TARGET_WORKLOAD} "
        f"deployment CPU limit to {CPU_DEMO_TARGET_CPU_LIMIT}. Do not change CPU requests, memory, replicas, autoscaling, or any other resource."
    )


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
    target_site = site
    workload = DEMO_WORKLOAD
    container_name = "nginx"
    level = "error"

    scenarios: dict[str, dict[str, Any]] = {
        "oom": {
            "message": "OOMKilled: container exceeded memory limits",
            "confidence_override": 0.95,
            "failure_type_override": "OOMKilled",
        },
        "lightspeed": {
            "message": "Ansible playbook generation requested for edge recovery",
            "confidence_override": 0.95,
            "failure_type_override": "DNSFailure",
        },
        "escalation": {
            "message": "Kernel panic and persistent data corruption - requires human escalation",
            "confidence_override": 0.3,
            "failure_type_override": "Unknown",
        },
    }
    if normalized == CPU_DEMO_SCENARIO:
        target_site = CPU_DEMO_SITE_A
        workload = CPU_DEMO_SOURCE_WORKLOAD
        container_name = CPU_DEMO_SOURCE_WORKLOAD
        level = "warn"
        spec = {
            "message": _cpu_misconfiguration_message(CPU_DEMO_SITE_A, CPU_DEMO_SITE_B),
        }
    else:
        spec = scenarios.get(
            normalized,
            {
                "message": "CrashLoopBackOff: nginx worker stalled after a transient failure; restart required",
                "confidence_override": 0.95,
                "failure_type_override": "CrashLoopBackOff",
            },
        )
        if normalized not in scenarios:
            normalized = "crashloop"

    event: dict[str, Any] = {
        "@timestamp": now,
        "incident_id": incident_id,
        "message": spec["message"],
        "level": level,
        "kubernetes": {
            "namespace_name": EDGE_NAMESPACE,
            "pod_name": workload,
            "container_name": container_name,
        },
        "labels": {
            "edge_site_id": target_site,
            "dark_noc_demo": "true",
            "dark_noc_scenario": normalized,
        },
    }
    if "confidence_override" in spec:
        event["_overrides"] = {
            "confidence_override": spec["confidence_override"],
            "failure_type_override": spec["failure_type_override"],
        }
    return event


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
