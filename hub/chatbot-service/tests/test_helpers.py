"""Unit tests for helper functions: SLO computation, demo events, normalization.

build_deps() and normalize_session_id() are tested in hub/shared/tests
instead, since that's where they now live.
"""

import pytest
from chatbot_service import (
    build_demo_event,
    build_incident_movie,
    compute_slo_metrics,
    normalize_incident_record,
)


class TestNormalizeIncidentRecord:
    def test_flat_record(self):
        payload = {
            "timestamp": "2026-06-14T10:00:00Z",
            "incident_id": "inc-001",
            "failure_type": "oom",
            "severity": "high",
            "remediation_action": "restart",
            "remediation_success": True,
            "servicenow_ticket": "INC001",
            "aap_job_id": "42",
            "ai_confidence": 0.92,
            "total_duration_ms": 15000,
        }
        result = normalize_incident_record(payload)
        assert result["incident_id"] == "inc-001"
        assert result["failure_type"] == "oom"
        assert result["remediation_success"] is True
        assert result["ai_confidence"] == 0.92

    def test_nested_event_envelope(self):
        payload = {
            "event": {
                "@timestamp": "2026-06-14T10:00:00Z",
                "failure_type": "crashloop",
                "severity": "medium",
                "remediation_action": "detected",
                "remediation_success": False,
                "labels": {"edge_site_id": "edge-02"},
            }
        }
        result = normalize_incident_record(payload)
        assert result["failure_type"] == "crashloop"
        assert result["edge_site_id"] == "edge-02"
        assert result["remediation_success"] is False

    def test_missing_fields_get_defaults(self):
        payload = {"message": "something happened"}
        result = normalize_incident_record(payload)
        assert result["failure_type"] == "unknown"
        assert result["remediation_action"] == "detected"
        assert result["edge_site_id"] == "edge-site-01"
        assert result["ai_confidence"] == 0


class TestComputeSloMetrics:
    def test_empty_records(self):
        slo = compute_slo_metrics([], 5, 7)
        assert slo["sample_size"] == 0
        assert slo["mttr_seconds"] is None
        assert slo["incidents_per_hour"] == 0.0
        assert slo["platform_availability_pct"] == pytest.approx(71.43, abs=0.01)

    def test_with_records(self):
        records = [
            {
                "remediation_success": True,
                "servicenow_ticket": "",
                "remediation_action": "restart",
                "aap_job_id": "1",
                "ai_confidence": 0.9,
                "total_duration_ms": 10000,
            },
            {
                "remediation_success": False,
                "servicenow_ticket": "INC002",
                "remediation_action": "escalated",
                "aap_job_id": "",
                "ai_confidence": 0.4,
                "total_duration_ms": 30000,
            },
        ]
        slo = compute_slo_metrics(records, 7, 7)
        assert slo["sample_size"] == 2
        assert slo["mttr_seconds"] == 20.0
        assert slo["auto_remediation_pct"] == 50.0
        assert slo["escalation_pct"] == 50.0
        assert slo["aap_success_pct"] == 100.0
        assert slo["ai_confidence_avg"] == pytest.approx(0.65, abs=0.01)
        assert slo["platform_availability_pct"] == 100.0


class TestBuildIncidentMovie:
    def test_empty_records(self):
        movie, impact = build_incident_movie([], {"mttr_seconds": None})
        assert movie == []
        assert impact["incidents_processed"] == 0

    def test_with_records(self):
        records = [
            {
                "timestamp": "2026-06-14T10:00:00+00:00",
                "incident_id": "inc-1",
                "failure_type": "oom",
                "edge_site_id": "edge-site-01",
                "remediation_action": "restart",
                "remediation_success": True,
                "servicenow_ticket": "",
                "aap_job_id": "5",
                "ai_confidence": 0.9,
                "total_duration_ms": 12000,
            },
            {
                "timestamp": "2026-06-14T09:00:00+00:00",
                "incident_id": "inc-2",
                "failure_type": "crashloop",
                "edge_site_id": "edge-site-01",
                "remediation_action": "escalated",
                "remediation_success": False,
                "servicenow_ticket": "INC123",
                "aap_job_id": "",
                "ai_confidence": 0.5,
                "total_duration_ms": 0,
            },
        ]
        movie, impact = build_incident_movie(records, {"mttr_seconds": 12.0})
        assert len(movie) == 2
        assert movie[0]["stage"] == "Auto-Remediated"
        assert movie[1]["stage"] == "Escalated"
        assert impact["incidents_processed"] == 2
        assert impact["tickets_avoided"] == 1
        assert impact["escalated_tickets"] == 1


class TestBuildDemoEvent:
    def test_crashloop(self):
        event = build_demo_event("crashloop", "edge-site-01", "test-id-1")
        assert event["labels"]["dark_noc_scenario"] == "crashloop"
        assert "CrashLoopBackOff" in event["message"]
        assert event["kubernetes"]["namespace_name"] == "dark-noc-edge"
        assert event["labels"]["edge_site_id"] == "edge-site-01"
        assert event["incident_id"] == "test-id-1"
        assert event["_overrides"] == {"confidence_override": 0.95, "failure_type_override": "CrashLoopBackOff"}

    def test_oom(self):
        event = build_demo_event("oom", "edge-02", "test-id-2")
        assert event["labels"]["dark_noc_scenario"] == "oom"
        assert "OOMKilled" in event["message"]
        assert event["labels"]["edge_site_id"] == "edge-02"
        assert event["_overrides"] == {"confidence_override": 0.95, "failure_type_override": "OOMKilled"}

    def test_lightspeed(self):
        event = build_demo_event("lightspeed", "edge-site-01", "test-id-3")
        assert event["labels"]["dark_noc_scenario"] == "lightspeed"
        assert "playbook" in event["message"].lower()

    def test_escalation(self):
        event = build_demo_event("escalation", "edge-site-01", "test-id-4")
        assert event["labels"]["dark_noc_scenario"] == "escalation"
        assert "escalation" in event["message"].lower()

    def test_unknown_scenario_defaults_to_crashloop(self):
        event = build_demo_event("unknown-thing", "edge-site-01", "test-id-5")
        assert event["labels"]["dark_noc_scenario"] == "crashloop"

    def test_cpu_misconfiguration_same_site(self, monkeypatch):
        import chatbot_service.kafka as demo_kafka

        monkeypatch.setattr(demo_kafka, "EDGE_NAMESPACE", "edge-workloads")
        monkeypatch.setattr(demo_kafka, "CPU_DEMO_SITE_A", "edge-site-01")
        monkeypatch.setattr(demo_kafka, "CPU_DEMO_SITE_B", "edge-site-01")

        event = build_demo_event("cpu-misconfiguration", "ignored-site", "cpu-demo-1")

        assert event["level"] == "warn"
        assert event["labels"] == {
            "edge_site_id": "edge-site-01",
            "dark_noc_demo": "true",
            "dark_noc_scenario": "cpu-misconfiguration",
        }
        assert event["kubernetes"] == {
            "namespace_name": "edge-workloads",
            "pod_name": "microservice-a",
            "container_name": "microservice-a",
        }
        assert "same cluster (edge-site-01)" in event["message"]
        assert "different cluster" not in event["message"]
        assert "get_pods for namespace edge-workloads on edge-site-01" in event["message"]
        assert "get_pod_logs with edge_site_id=edge-site-01" in event["message"]
        assert "get_pod_spec" in event["message"]
        assert "only mutation" in event["message"]
        assert "CPU limit to 250m" in event["message"]
        assert "Do not change CPU requests, memory, replicas, autoscaling" in event["message"]
        assert "_overrides" not in event

    def test_cpu_misconfiguration_distinct_sites(self, monkeypatch):
        import chatbot_service.kafka as demo_kafka

        monkeypatch.setattr(demo_kafka, "EDGE_NAMESPACE", "edge-workloads")
        monkeypatch.setattr(demo_kafka, "CPU_DEMO_SITE_A", "edge-site-01")
        monkeypatch.setattr(demo_kafka, "CPU_DEMO_SITE_B", "edge-site-02")

        event = build_demo_event("cpu-misconfiguration", "ignored-site", "cpu-demo-2")

        assert event["labels"]["edge_site_id"] == "edge-site-01"
        assert "different cluster (edge-site-02), not on edge-site-01" in event["message"]
        assert "get_pods for namespace edge-workloads on both edge-site-01 and edge-site-02" in event["message"]
        assert "edge_site_id=edge-site-01 for microservice-a" in event["message"]
        assert "edge_site_id=edge-site-02 for microservice-b" in event["message"]
        assert "MUST pass edge_site_id=edge-site-02 and namespace=edge-workloads" in event["message"]
        assert "Do NOT use LokiStack search_logs" in event["message"]
        assert "Do not change CPU requests, memory, replicas, autoscaling" in event["message"]


@pytest.mark.parametrize("envelope", [None, "event", "payload"])
def test_cpu_generation_failure_timeline_keeps_lightspeed_artifact_separate(envelope):
    audit_record = {
        "failure_type": "CPUThrottling",
        "remediation_action": "generate-playbook",
        "remediation_success": False,
        "lightspeed_conversation_id": "conv-123",
        "remediation_summary": "Generated playbook failed validation: Invalid YAML",
    }
    record = normalize_incident_record({envelope: audit_record} if envelope else audit_record)
    movie, _ = build_incident_movie([record], {})
    assert movie[0]["title"].startswith("CPUThrottling")
    assert movie[0]["summary"] == audit_record["remediation_summary"]
    assert movie[0]["artifacts"]["lightspeed_conversation_id"] == "conv-123"
    assert movie[0]["artifacts"]["aap_job_id"] is None
    assert compute_slo_metrics([record], 1, 1)["aap_success_pct"] is None
