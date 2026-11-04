import json
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.network


INCIDENT_STATE_FIELDS = {
    "raw_event",
    "kafka_offset",
    "log_event",
    "incident_id",
    "incident_start_ms",
    "context_snippets",
    "rag_query_used",
    "root_cause_analysis",
    "analysis_tokens_used",
    "analysis_latency_ms",
    "decision",
    "selected_template",
    "remediation_result",
    "pod_status",
    "cluster_events",
    "pod_logs",
    "resource_specs",
    "deployment_observations",
    "log_search_results",
    "recent_errors",
    "slack_thread_ts",
    "servicenow_ticket",
    "fast_path_actuation",
    "total_duration_ms",
    "error_message",
}


_KNOWN_PLAYBOOK_TYPES = {"OOMKilled", "CrashLoopBackOff", "ConfigError", "NetworkTimeout", "StorageFull"}
_MIN_REMEDIATION_CONFIDENCE = 0.8
_ANALYSIS_FAILURE_SUMMARY = "Analysis failed, escalate for manual review"


def _make_raw_event(message):
    return json.dumps(
        {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "level": "error",
            "kubernetes": {
                "namespace_name": os.environ.get("EDGE_NAMESPACE", "dark-noc-edge"),
                "pod_name": os.environ.get("DEMO_WORKLOAD", "edge-nginx"),
                "container_name": "nginx",
            },
            "labels": {"edge_site_id": os.environ.get("EDGE_SITE_ID", "edge-site-01")},
        }
    )


def _assert_valid_remediation_response(response, expected_raw_event):
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == INCIDENT_STATE_FIELDS
    assert body["raw_event"] == expected_raw_event
    assert body["incident_id"]
    assert body["log_event"]["raw"] == expected_raw_event
    assert not body["error_message"], body
    assert body["total_duration_ms"] > 0
    rca = body["root_cause_analysis"]
    assert rca["summary"] and rca["summary"] != _ANALYSIS_FAILURE_SUMMARY, body
    assert 0 <= rca["confidence"] <= 1
    assert isinstance(rca["evidence"], list), body
    _assert_decision_outcome(body)


def _assert_decision_outcome(body):
    decision = body["decision"]
    remediation = body["remediation_result"]
    rca = body["root_cause_analysis"]
    if decision == "escalate":
        assert body["servicenow_ticket"], body
        assert remediation is None, body
        return
    assert decision in {"remediate", "lightspeed"}, body
    assert rca["confidence"] >= _MIN_REMEDIATION_CONFIDENCE, body
    assert remediation and remediation["output_summary"], body
    if decision == "remediate":
        assert rca["failure_type"] in _KNOWN_PLAYBOOK_TYPES, body
        assert body["selected_template"] and remediation["tool_used"] == "aap", body
    else:
        assert rca["selected_action"] and rca["selected_action"]["evidence"], body
        assert remediation["tool_used"] in {"lightspeed", "aap"}, body
    if not remediation["success"]:
        assert body["servicenow_ticket"], body
        return
    if remediation["tool_used"] == "aap":
        assert remediation["job_id"], body
    if decision == "lightspeed":
        assert remediation["generated_playbook_name"] and remediation["generated_playbook_preview"], body


class TestRemediateScenarios:
    # Live RCA and match-guard verdicts vary. Exact routing is covered by unit tests;
    # these scenarios require a real diagnosis and the side effects of its chosen route.
    @pytest.mark.parametrize(
        "message",
        [
            pytest.param(
                "nginx worker stalled after a transient failure; pod is in CrashLoopBackOff, "
                "restart count 6, last exit code 1. Configuration validation succeeded; "
                "memory and disk usage are within limits.",
                id="known-playbook",
            ),
            pytest.param(
                "nginx upstream requests fail: host not found in upstream api.internal.svc.cluster.local. "
                "DNS queries time out while direct connections to the upstream service IP succeed. "
                "The failure persists across pod restarts; inspect resolver configuration and DNS service health.",
                id="generated-playbook",
            ),
            pytest.param(
                "Intermittent request failures reported by clients; available logs end before the failures. "
                "No failing dependency, resource condition or reproducible error has been identified. "
                "The affected component and safe corrective change are unknown.",
                id="escalation",
            ),
        ],
    )
    def test_real_rca_completes_workflow(self, agent_service_client, message):
        raw_event = _make_raw_event(message)
        response = agent_service_client.post("/remediate", json={"raw_event": raw_event})

        _assert_valid_remediation_response(response, raw_event)
