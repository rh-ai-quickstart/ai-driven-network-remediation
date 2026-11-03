import pytest
from pydantic import ValidationError

from agent_service.models import (
    GraphConfig,
    IncidentState,
    LogEvent,
    RemediationResult,
    RemediationTarget,
    RootCauseAnalysis,
    SelectedAction,
)


class TestSelectedAction:
    @pytest.mark.parametrize("namespace, expected", [("  ", ""), (" prod ", "prod")])
    def test_normalizes_namespace_whitespace(self, namespace, expected):
        target = RemediationTarget(edge_site_id="edge-1", kind="CustomResource", name="default", namespace=namespace)
        assert target.namespace == expected

    def test_cluster_scope_requires_an_explicit_declaration(self):
        target = RemediationTarget(edge_site_id="edge-1", kind="CustomResource", name="default")
        assert target.cluster_scoped is False
        cluster_target = RemediationTarget(
            edge_site_id="edge-1", kind="ClusterConfiguration", name="default", cluster_scoped=True
        )
        assert cluster_target.cluster_scoped is True

    def test_accepts_arbitrary_resource_and_json_parameters(self):
        selection = SelectedAction(
            goal="Repair the observed cluster DNS configuration.",
            target=RemediationTarget(edge_site_id="edge-1", kind="DNS", name="default"),
            evidence=["Resolver points to an unavailable server"],
            parameters={"upstreams": ["10.0.0.10"], "enabled": True, "retries": 3},
            verification=["DNS queries resolve successfully"],
        )
        assert selection.target.namespace == ""
        assert selection.parameters["enabled"] is True
        assert selection.parameters["retries"] == 3
        assert selection.parameters["upstreams"] == ["10.0.0.10"]

    @pytest.mark.parametrize("field", ["goal", "evidence", "verification"])
    @pytest.mark.parametrize("blank", ["", " "])
    def test_rejects_blank_remediation_requirements(self, field, blank):
        selection = {
            "goal": "Increase the observed CPU limit.",
            "target": {"edge_site_id": "edge-1", "kind": "Deployment", "name": "worker"},
            "evidence": ["CPU throttling"],
            "verification": ["CPU throttling subsides"],
        }
        selection[field] = blank if field == "goal" else [blank]
        with pytest.raises(ValidationError):
            SelectedAction(**selection)

    def test_rejects_empty_evidence(self):
        selection = {
            "goal": "Increase the observed CPU limit.",
            "target": {"edge_site_id": "edge-1", "kind": "Deployment", "name": "worker"},
            "evidence": ["CPU throttling"],
            "verification": ["CPU throttling subsides"],
        }
        selection["evidence"] = []
        with pytest.raises(ValidationError):
            SelectedAction(**selection)

    @pytest.mark.parametrize("verification_fields", [{}, {"verification": []}])
    def test_verification_can_be_omitted_or_empty(self, verification_fields):
        selection = SelectedAction(
            goal="Increase the observed CPU limit.",
            target=RemediationTarget(edge_site_id="edge-1", kind="Deployment", name="worker"),
            evidence=["CPU throttling"],
            **verification_fields,
        )
        assert selection.verification == []

    def test_schema_requires_evidence_but_not_verification(self):
        schema = SelectedAction.model_json_schema()
        assert "evidence" in schema["required"]
        assert schema["properties"]["evidence"]["minItems"] == 1
        assert "verification" not in schema["required"]

    @pytest.mark.parametrize("field", ["edge_site_id", "kind", "name"])
    def test_rejects_blank_target_identity(self, field):
        target = {"edge_site_id": "edge-1", "kind": "CustomResource", "name": "worker"}
        target[field] = " "
        with pytest.raises(ValidationError):
            RemediationTarget(**target)


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
        removed_fields = {
            "awaiting_human_approval",
            "notifications_sent",
            "execution_result",
            "failed_attempts",
            "should_retry",
            "langfuse_trace_id",
        }
        state = IncidentState(raw_event="pod crashloop")
        schema_fields = IncidentState.model_json_schema()["properties"]
        serialized_fields = state.model_dump()

        assert removed_fields.isdisjoint(IncidentState.model_fields)
        assert removed_fields.isdisjoint(schema_fields)
        assert removed_fields.isdisjoint(serialized_fields)

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
        assert state.total_duration_ms == 0.0
        assert state.error_message == ""

    def test_investigation_fields_have_defaults(self):
        state = IncidentState(raw_event="pod crashloop")
        assert state.cluster_events == []
        assert state.pod_logs == ""
        assert state.log_search_results == []

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

    def test_investigation_defaults_are_coherent(self):
        config = GraphConfig()
        # A single tool call must fit within the overall investigation budget.
        assert config.investigate_timeout == 120
        assert 0 < config.tool_call_timeout < config.investigate_timeout
        # The loop must allow at least one investigate iteration.
        assert config.investigate_max_iterations >= 1

    def test_custom_thresholds(self):
        config = GraphConfig(remediate_threshold=0.9, escalate_threshold=0.6)
        assert config.remediate_threshold == 0.9
        assert config.escalate_threshold == 0.6

    def test_investigate_max_iterations_from_env(self, monkeypatch):
        monkeypatch.setenv("INVESTIGATE_MAX_ITERATIONS", "25")
        config = GraphConfig()
        assert config.investigate_max_iterations == 25

    def test_investigate_max_iterations_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("INVESTIGATE_MAX_ITERATIONS", "25")
        config = GraphConfig(investigate_max_iterations=5)
        assert config.investigate_max_iterations == 5
