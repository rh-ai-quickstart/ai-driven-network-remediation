import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import Draft202012Validator

from agent_service.models import (
    IncidentState,
    LogEvent,
    RemediationResult,
    RootCauseAnalysis,
)
from agent_service.nodes.audit import (
    audit_node,
    build_audit_payload,
    publish_audit_record,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INCIDENT_AUDIT_SCHEMA = json.loads((_REPO_ROOT / "contracts" / "incident-audit.schema.json").read_text())
_SCHEMA_VALIDATOR = Draft202012Validator(_INCIDENT_AUDIT_SCHEMA)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_LOG_EVENT = LogEvent(
    timestamp="2024-01-01T00:00:00Z",
    message="OOMKilled: container exceeded memory limits",
    level="error",
    namespace="dark-noc-edge",
    pod_name="nginx-edge-oom",
    container="nginx",
    edge_site_id="edge-01",
    kafka_offset=7,
    raw="{}",
)

_RCA = RootCauseAnalysis(
    failure_type="OOMKilled",
    confidence=0.92,
    summary="Memory limit exceeded",
    evidence=["OOMKilled in pod events"],
    recommended_actions=["restart-nginx"],
    estimated_severity="high",
    runbook_reference="restart-nginx",
)

_REMEDIATION = RemediationResult(
    action_taken="restart-nginx",
    tool_used="aap",
    success=True,
    job_id="101",
    duration_seconds=12.5,
    output_summary="PLAY RECAP ok",
    timestamp="2024-01-01T00:01:00Z",
)

_REMEDIATION_FAILED = RemediationResult(
    action_taken="restart-nginx",
    tool_used="aap",
    success=False,
    job_id="",
    duration_seconds=30.0,
    output_summary="PLAY RECAP failed",
    timestamp="2024-01-01T00:01:00Z",
)


def _assert_valid_schema(payload: dict) -> None:
    errors = sorted(_SCHEMA_VALIDATOR.iter_errors(payload), key=lambda e: e.path)
    assert not errors, "; ".join(f"{e.json_path}: {e.message}" for e in errors)
    assert _TIMESTAMP_RE.match(payload["timestamp"])


@pytest.mark.parametrize(
    "state_kwargs",
    [
        pytest.param(
            {
                "raw_event": "{}",
                "incident_id": "550e8400-e29b-41d4-a716-446655440000",
                "log_event": _LOG_EVENT,
                "root_cause_analysis": _RCA,
                "remediation_result": _REMEDIATION,
                "decision": "remediate",
                "total_duration_ms": 5000.0,
            },
            id="remediate_happy_path",
        ),
        pytest.param(
            {
                "raw_event": "{}",
                "incident_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "log_event": _LOG_EVENT,
                "root_cause_analysis": _RCA.model_copy(update={"estimated_severity": "critical"}),
                "decision": "escalate",
                "servicenow_ticket": "INC001234",
                "total_duration_ms": 8000.0,
            },
            id="escalate_with_servicenow",
        ),
        pytest.param(
            {
                "raw_event": "plain alert",
                "incident_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
            },
            id="minimal_state",
        ),
    ],
)
def test_build_audit_payload_matches_incident_audit_schema(state_kwargs):
    state = IncidentState(**state_kwargs)
    payload = build_audit_payload(state)
    _assert_valid_schema(payload)


class TestBuildAuditPayload:
    def test_builds_schema_required_fields(self):
        state = IncidentState(
            raw_event="{}",
            incident_id="550e8400-e29b-41d4-a716-446655440000",
            log_event=_LOG_EVENT,
            root_cause_analysis=_RCA,
            remediation_result=_REMEDIATION,
            decision="remediate",
            incident_start_ms=1_000_000.0,
            total_duration_ms=5000.0,
        )

        payload = build_audit_payload(state)

        assert payload["incident_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert payload["failure_type"] == "OOMKilled"
        assert payload["severity"] == "high"
        assert payload["edge_site_id"] == "edge-01"
        assert payload["ai_confidence"] == 0.92
        assert payload["remediation_action"] == "restart-nginx"
        assert payload["remediation_success"] is True
        assert payload["aap_job_id"] == "101"
        assert payload["total_duration_ms"] == 5000.0
        _assert_valid_schema(payload)

    def test_maps_unknown_failure_type_to_schema_enum(self):
        rca = _RCA.model_copy(update={"failure_type": "KafkaLag"})
        state = IncidentState(
            raw_event="{}",
            log_event=_LOG_EVENT,
            root_cause_analysis=rca,
            decision="lightspeed",
        )

        payload = build_audit_payload(state)

        assert payload["failure_type"] == "Unknown"
        assert payload["remediation_action"] == "lightspeed"
        assert payload["remediation_success"] is False
        assert "aap_job_id" not in payload
        _assert_valid_schema(payload)

    @patch("agent_service.nodes.audit.time.time", return_value=1005.0)
    def test_computes_duration_from_incident_start_when_not_set(self, _mock_time):
        state = IncidentState(
            raw_event="{}",
            log_event=_LOG_EVENT,
            root_cause_analysis=_RCA,
            decision="escalate",
            incident_start_ms=1_000_000.0,
            total_duration_ms=0.0,
        )

        payload = build_audit_payload(state)

        assert payload["total_duration_ms"] == 5000.0

    def test_includes_servicenow_ticket_on_escalate(self):
        state = IncidentState(
            raw_event="{}",
            log_event=_LOG_EVENT,
            root_cause_analysis=_RCA,
            decision="escalate",
            servicenow_ticket="INC0098765",
            total_duration_ms=3000.0,
        )

        payload = build_audit_payload(state)

        assert payload["servicenow_ticket"] == "INC0098765"
        assert payload["remediation_action"] == "escalate"
        assert payload["remediation_success"] is False
        assert "aap_job_id" not in payload
        _assert_valid_schema(payload)

    def test_failed_remediation_publishes_remediation_success_false(self):
        state = IncidentState(
            raw_event="{}",
            log_event=_LOG_EVENT,
            root_cause_analysis=_RCA,
            remediation_result=_REMEDIATION_FAILED,
            decision="remediate",
            total_duration_ms=4500.0,
        )

        payload = build_audit_payload(state)

        assert payload["remediation_success"] is False
        assert payload["remediation_action"] == "restart-nginx"
        assert "aap_job_id" not in payload
        _assert_valid_schema(payload)

    def test_minimal_state_uses_defaults(self):
        # Complements minimal_state in the parametrized schema test with explicit field checks.
        state = IncidentState(
            raw_event="alert",
            incident_id="7c9e6679-7425-40de-944b-e07fc1f90ae7",
        )

        payload = build_audit_payload(state)

        assert payload["failure_type"] == "Unknown"
        assert payload["severity"] == "medium"
        assert payload["edge_site_id"] == "unknown"
        assert payload["remediation_action"] == "none"
        assert payload["remediation_success"] is False
        assert payload["ai_confidence"] == 0.0
        assert "servicenow_ticket" not in payload
        assert "aap_job_id" not in payload


class TestPublishAuditRecord:
    @patch("agent_service.nodes.audit.KafkaProducer")
    def test_publishes_json_payload(self, mock_producer_cls):
        mock_producer = MagicMock()
        mock_future = MagicMock()
        mock_future.get.return_value = MagicMock(offset=15)
        mock_producer.send.return_value = mock_future
        mock_producer_cls.return_value = mock_producer

        payload = {"incident_id": "abc", "remediation_success": True}
        offset = publish_audit_record(
            payload,
            bootstrap_servers="kafka.test:9092",
            topic="custom-audit",
        )

        assert offset == 15
        mock_producer_cls.assert_called_once_with(
            bootstrap_servers="kafka.test:9092",
            value_serializer=mock_producer_cls.call_args.kwargs["value_serializer"],
        )
        sent_value = mock_producer.send.call_args.kwargs["value"]
        assert sent_value == payload
        mock_producer.send.assert_called_once_with("custom-audit", value=payload)
        mock_producer.close.assert_called_once_with(timeout=10)

        serialized = mock_producer_cls.call_args.kwargs["value_serializer"](payload)
        assert json.loads(serialized.decode("utf-8")) == payload


class TestAuditNode:
    @patch("agent_service.nodes.audit.publish_audit_record", return_value=22)
    def test_publishes_and_returns_duration(self, mock_publish):
        state = IncidentState(
            raw_event="{}",
            log_event=_LOG_EVENT,
            root_cause_analysis=_RCA,
            remediation_result=_REMEDIATION,
            decision="remediate",
            total_duration_ms=9000.0,
        )

        result = audit_node(state)

        assert result == {"total_duration_ms": 9000.0}
        mock_publish.assert_called_once()
        payload = mock_publish.call_args.args[0]
        assert payload["incident_id"] == state.incident_id
        assert payload["remediation_success"] is True
        _assert_valid_schema(payload)

    @patch("agent_service.nodes.audit.publish_audit_record", side_effect=RuntimeError("kafka down"))
    def test_publish_failure_does_not_raise(self, _mock_publish):
        state = IncidentState(
            raw_event="{}",
            log_event=_LOG_EVENT,
            root_cause_analysis=_RCA,
            decision="escalate",
            total_duration_ms=1200.0,
        )

        result = audit_node(state)

        assert result == {"total_duration_ms": 1200.0}
