import json

from agent_service.models import IncidentState, LogEvent, RootCauseAnalysis

CANONICAL_RAW_EVENT = json.dumps(
    {
        "@timestamp": "2024-01-01T00:00:00Z",
        "message": "CrashLoopBackOff",
        "level": "error",
        "kubernetes": {
            "namespace_name": "prod",
            "pod_name": "nginx-abc123",
            "container_name": "nginx",
        },
        "labels": {
            "edge_site_id": "edge-1",
        },
    }
)


def make_log_event(**overrides):
    defaults = dict(
        timestamp="2024-01-01T00:00:00Z",
        message="CrashLoopBackOff",
        level="error",
        namespace="prod",
        pod_name="nginx-abc123",
        container="nginx",
        edge_site_id="edge-1",
        kafka_offset=42,
        raw=CANONICAL_RAW_EVENT,
    )
    defaults.update(overrides)
    return LogEvent(**defaults)


def make_rca(**overrides):
    defaults = dict(
        failure_type="OOMKilled",
        confidence=0.92,
        summary="Container killed by OOM",
        evidence=["memory spike at 14:32"],
        recommended_actions=["increase memory limit"],
        estimated_severity="high",
        runbook_reference="runbook-oom-001",
    )
    defaults.update(overrides)
    return RootCauseAnalysis(**defaults)


def make_state(**overrides):
    defaults = dict(raw_event=CANONICAL_RAW_EVENT, log_event=make_log_event())
    defaults.update(overrides)
    return IncidentState(**defaults)
