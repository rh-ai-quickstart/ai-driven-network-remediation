import pytest
from pydantic import ValidationError

from agent_service.models import (
    GraphConfig,
    IncidentState,
    LogEvent,
    RemediationResult,
    RootCauseAnalysis,
)


class TestRootCauseAnalysis:
    def test_valid_analysis(self):
        rca = RootCauseAnalysis(
            failure_type="OOMKilled",
            confidence=0.9,
            summary="Pod exceeded memory limit",
            evidence=["OOMKilled in dmesg", "container restart count=5"],
            recommended_actions=["increase memory limit", "check for memory leaks"],
            estimated_severity="high",
            runbook_reference="runbooks/oomkilled.md",
        )
        assert rca.confidence == 0.9
        assert rca.failure_type == "OOMKilled"
        assert rca.estimated_severity == "high"
        assert len(rca.evidence) == 2
        assert len(rca.recommended_actions) == 2
        assert rca.runbook_reference == "runbooks/oomkilled.md"

    def test_confidence_must_be_float(self):
        with pytest.raises(ValidationError):
            RootCauseAnalysis(
                failure_type="OOMKilled",
                confidence="not_a_number",
                summary="test",
                evidence=[],
                recommended_actions=[],
                estimated_severity="low",
                runbook_reference="test",
            )

    def test_invalid_failure_type_rejected(self):
        with pytest.raises(ValidationError):
            RootCauseAnalysis(
                failure_type="NotARealType",
                confidence=0.5,
                summary="test",
                evidence=[],
                recommended_actions=[],
                estimated_severity="low",
                runbook_reference="test",
            )

    def test_invalid_estimated_severity_rejected(self):
        with pytest.raises(ValidationError):
            RootCauseAnalysis(
                failure_type="OOMKilled",
                confidence=0.5,
                summary="test",
                evidence=[],
                recommended_actions=[],
                estimated_severity="ultra_high",
                runbook_reference="test",
            )


class TestIncidentState:
    def test_valid_state(self):
        state = IncidentState(raw_event="pod crashloop")
        assert state.raw_event == "pod crashloop"
        assert state.context_snippets == []
        assert state.root_cause_analysis is None
        assert state.decision == ""
        assert state.remediation_result is None

    def test_old_fields_removed(self):
        fields = set(IncidentState.model_fields.keys())
        assert "awaiting_human_approval" not in fields
        assert "notifications_sent" not in fields
        assert "execution_result" not in fields

    def test_new_fields_have_defaults(self):
        state = IncidentState(raw_event="pod crashloop")
        assert state.log_event is None
        assert state.incident_id != ""
        assert state.incident_start_ms > 0
        assert state.confidence_override is None

    def test_rag_and_analysis_fields_have_defaults(self):
        state = IncidentState(raw_event="pod crashloop")
        assert state.rag_query_used == ""
        assert state.analysis_tokens_used == 0
        assert state.analysis_latency_ms == 0.0

    def test_integration_ready_fields_have_defaults(self):
        state = IncidentState(raw_event="pod crashloop")
        assert state.pod_status == {}
        assert state.recent_errors == []
        assert state.slack_thread_ts == ""
        assert state.servicenow_ticket == ""
        assert state.langfuse_trace_id == ""
        assert state.total_duration_ms == 0.0
        assert state.error_message == ""

    def test_state_with_log_event(self):
        event = LogEvent(
            timestamp="2024-01-01T00:00:00Z",
            message="crash",
            level="error",
            namespace="prod",
            pod_name="nginx-abc",
            container="nginx",
            edge_site_id="edge-01",
            kafka_offset=1,
            raw="raw",
        )
        state = IncidentState(raw_event="pod crashloop", log_event=event)
        assert state.log_event.message == "crash"

    def test_state_with_root_cause_analysis(self):
        rca = RootCauseAnalysis(
            failure_type="OOMKilled",
            confidence=0.95,
            summary="Memory limit exceeded",
            evidence=["OOMKilled event"],
            recommended_actions=["increase memory"],
            estimated_severity="critical",
            runbook_reference="runbooks/oomkilled.md",
        )
        state = IncidentState(raw_event="pod crashloop", root_cause_analysis=rca)
        assert state.root_cause_analysis.confidence == 0.95
        assert state.root_cause_analysis.failure_type == "OOMKilled"


class TestLogEvent:
    def test_valid_log_event(self):
        event = LogEvent(
            timestamp="2024-01-01T00:00:00Z",
            message="pod crash detected",
            level="error",
            namespace="prod",
            pod_name="nginx-abc123",
            container="nginx",
            edge_site_id="edge-01",
            kafka_offset=42,
            raw='{"msg": "pod crash detected"}',
        )
        assert event.message == "pod crash detected"
        assert event.kafka_offset == 42

    def test_kafka_offset_must_be_int(self):
        with pytest.raises(ValidationError):
            LogEvent(
                timestamp="2024-01-01T00:00:00Z",
                message="test",
                level="info",
                namespace="default",
                pod_name="test",
                container="test",
                edge_site_id="edge-01",
                kafka_offset="not_an_int",
                raw="raw",
            )


class TestRemediationResult:
    def test_valid_result(self):
        result = RemediationResult(
            action_taken="restart pod",
            tool_used="openshift",
            success=True,
            job_id="job-123",
            duration_seconds=1.5,
            output_summary="Pod restarted successfully",
            timestamp="2024-01-01T00:00:00Z",
        )
        assert result.success is True
        assert result.action_taken == "restart pod"
        assert result.generated_template_name is None

    def test_valid_result_with_lightspeed_fields(self):
        result = RemediationResult(
            action_taken="generate playbook",
            tool_used="lightspeed",
            success=True,
            job_id="job-456",
            duration_seconds=3.2,
            output_summary="Playbook generated",
            timestamp="2024-01-01T00:00:00Z",
            generated_template_name="restart-template",
            generated_template_id="tmpl-789",
            generated_playbook_name="restart-playbook",
            generated_playbook_preview="- hosts: all\n  tasks: ...",
        )
        assert result.generated_template_name == "restart-template"
        assert result.generated_playbook_preview is not None

    def test_missing_required_field_rejected(self):
        with pytest.raises(ValidationError):
            RemediationResult(
                action_taken="restart pod",
                tool_used="openshift",
                success=True,
            )


class TestGraphConfig:
    def test_defaults(self):
        config = GraphConfig()
        assert config.remediate_threshold == 0.8
        assert config.escalate_threshold == 0.7

    def test_custom_thresholds(self):
        config = GraphConfig(remediate_threshold=0.9, escalate_threshold=0.6)
        assert config.remediate_threshold == 0.9
        assert config.escalate_threshold == 0.6
