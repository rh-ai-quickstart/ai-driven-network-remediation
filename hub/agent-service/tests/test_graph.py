from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from agent_service import main
from agent_service.graph import _route_after_act, build_graph
from agent_service.models import (
    GraphConfig,
    IncidentState,
    LogEvent,
    RemediationResult,
    RootCauseAnalysis,
)
from agent_service.nodes.remediate import make_remediate_node


def _analyze_stub(state: dict) -> dict:
    confidence = state.confidence_override if state.confidence_override is not None else 0.85
    failure_type = state.failure_type_override if state.failure_type_override is not None else "CrashLoopBackOff"
    rca = RootCauseAnalysis(
        failure_type=failure_type,
        confidence=confidence,
        summary="stub summary",
        evidence=["stub evidence"],
        recommended_actions=["stub action"],
        estimated_severity="medium",
        runbook_reference="stub-runbook",
    )
    return {"root_cause_analysis": rca}


def _make_analyze_stub(confidence: float, failure_type: str = "CrashLoopBackOff"):
    def analyze_node(state: dict) -> dict:
        rca = RootCauseAnalysis(
            failure_type=failure_type,
            confidence=confidence,
            summary="stub summary",
            evidence=["stub evidence"],
            recommended_actions=["stub action"],
            estimated_severity="medium",
            runbook_reference="stub-runbook",
        )
        return {"root_cause_analysis": rca}

    return analyze_node


class TestGraphCompilation:
    def test_graph_compiles(self):
        graph = build_graph()
        assert graph is not None

    def test_graph_has_remediate_node(self):
        graph = build_graph()
        node_names = {n.name for n in graph.get_graph().nodes.values()}
        assert "remediate" in node_names
        assert "execute" not in node_names

    def test_graph_has_lightspeed_node(self):
        graph = build_graph()
        node_names = {n.name for n in graph.get_graph().nodes.values()}
        assert "lightspeed" in node_names

    def test_graph_has_audit_node(self):
        graph = build_graph()
        node_names = {n.name for n in graph.get_graph().nodes.values()}
        assert "audit" in node_names

    def test_audit_is_terminal_node_before_end(self):
        graph = build_graph()
        g = graph.get_graph()
        end_sources = [e.source for e in g.edges if e.target == "__end__"]
        assert end_sources == ["audit"]

    def test_notify_connects_to_audit_not_end(self):
        graph = build_graph()
        g = graph.get_graph()
        notify_targets = [e.target for e in g.edges if e.source == "notify"]
        assert "audit" in notify_targets
        assert "__end__" not in notify_targets

    def test_graph_has_no_request_approval_node(self):
        graph = build_graph()
        node_names = {n.name for n in graph.get_graph().nodes.values()}
        assert "request_approval" not in node_names


class TestNormalizeNode:
    def test_normalize_produces_log_event(self):
        graph = build_graph()
        result = graph.invoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})
        assert result["log_event"] is not None
        assert isinstance(result["log_event"], LogEvent)
        assert "nginx CrashLoopBackOff" in result["log_event"].message


class TestRagRetrievalNode:
    def test_rag_retrieval_sets_rag_query_used(self):
        graph = build_graph()
        result = graph.invoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})
        assert result["rag_query_used"] != ""

    def test_rag_retrieval_sets_context_snippets(self):
        graph = build_graph()
        result = graph.invoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})
        assert len(result["context_snippets"]) > 0


class TestLinearFlow:
    def test_end_to_end_produces_expected_state(self):
        graph = build_graph()
        result = graph.invoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})

        assert result["raw_event"] == "nginx CrashLoopBackOff in namespace prod"
        assert result["log_event"] is not None
        assert isinstance(result["log_event"], LogEvent)
        assert len(result["context_snippets"]) > 0
        assert result["rag_query_used"] != ""
        assert result["root_cause_analysis"] is not None
        assert isinstance(result["root_cause_analysis"], RootCauseAnalysis)
        assert isinstance(result["root_cause_analysis"].confidence, float)
        assert result["root_cause_analysis"].failure_type is not None
        assert result["decision"] != ""


class TestConditionalRouting:
    def test_high_confidence_known_type_routes_through_remediate(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.85, "CrashLoopBackOff")):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "remediate"
        assert isinstance(result["remediation_result"], RemediationResult)
        assert result["remediation_result"].success is True
        assert result["remediation_result"].generated_template_name is None

    def test_high_confidence_generation_type_routes_through_lightspeed(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.85, "KafkaLag")):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "lightspeed"
        assert isinstance(result["remediation_result"], RemediationResult)
        assert result["remediation_result"].generated_template_name is not None
        assert result["remediation_result"].generated_playbook_name is not None

    def test_low_confidence_routes_through_escalate(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.5)):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "escalate"

    def test_low_confidence_escalates_regardless_of_failure_type(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.5, "KafkaLag")):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "escalate"
        assert result.get("remediation_result") is None

    def test_mid_confidence_routes_to_escalate(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.75)):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "escalate"

    def test_custom_thresholds_alter_routing(self):
        config = GraphConfig(remediate_threshold=0.9, escalate_threshold=0.8)
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.85)):
            graph = build_graph(config)
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "escalate"

    def test_custom_thresholds_route_to_lightspeed(self):
        config = GraphConfig(remediate_threshold=0.7, escalate_threshold=0.5)
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.75, "DNSFailure")):
            graph = build_graph(config)
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "lightspeed"
        assert result["remediation_result"].generated_template_name is not None


class TestConfidenceOverride:
    def test_confidence_override_controls_routing(self):
        graph = build_graph()
        result = graph.invoke({"raw_event": "test event", "confidence_override": 0.5})

        assert result["root_cause_analysis"].confidence == 0.5
        assert result["decision"] == "escalate"


class TestCli:
    def test_default_confidence_routes_to_remediate(self):
        runner = CliRunner()
        result = runner.invoke(main)

        assert result.exit_code == 0
        assert "next_action: remediate" in result.output
        assert "rca:" in result.output

    def test_low_confidence_routes_to_escalate(self):
        with patch("agent_service.graph.rag_retrieval_node", _rag_stub), \
             patch("agent_service.graph.analyze_node", _analyze_stub):
            runner = CliRunner()
            result = runner.invoke(main, ["--confidence", "0.5"])

        assert result.exit_code == 0
        assert "next_action: escalate" in result.output

    def test_lightspeed_route_via_failure_type(self):
        with patch("agent_service.graph.rag_retrieval_node", _rag_stub), \
             patch("agent_service.graph.analyze_node", _analyze_stub):
            runner = CliRunner()
            result = runner.invoke(main, ["--failure-type", "KafkaLag"])

        assert result.exit_code == 0
        assert "next_action: lightspeed" in result.output

    def test_mid_confidence_routes_to_escalate(self):
        with patch("agent_service.graph.rag_retrieval_node", _rag_stub), \
             patch("agent_service.graph.analyze_node", _analyze_stub):
            runner = CliRunner()
            result = runner.invoke(main, ["--confidence", "0.75"])

        assert result.exit_code == 0
        assert "next_action: escalate" in result.output

    def test_cli_output_shows_incident_id(self):
        runner = CliRunner()
        result = runner.invoke(main)

        assert result.exit_code == 0
        assert "incident_id:" in result.output


# -- Routing function tests --------------------------------------------------


class TestRouteAfterAct:
    def test_should_retry_routes_to_decide(self):
        state = IncidentState(raw_event="test", should_retry=True)
        assert _route_after_act(state) == "decide"

    def test_no_retry_routes_to_notify(self):
        state = IncidentState(raw_event="test", should_retry=False)
        assert _route_after_act(state) == "notify"

    def test_default_routes_to_notify(self):
        state = IncidentState(raw_event="test")
        assert _route_after_act(state) == "notify"


# -- Remediate node tests ----------------------------------------------------

_STUB_LOG_EVENT = LogEvent(
    timestamp="2024-01-01T00:00:00Z",
    message="pod crash",
    level="error",
    namespace="prod",
    pod_name="nginx-abc",
    container="nginx",
    edge_site_id="edge-1",
    kafka_offset=0,
    raw="raw log",
)

_STUB_RCA = RootCauseAnalysis(
    failure_type="CrashLoopBackOff",
    confidence=0.9,
    summary="pod is crash-looping",
    evidence=["restart count > 5"],
    recommended_actions=["restart-pod"],
    estimated_severity="high",
    runbook_reference="runbook-001",
)


def _mock_invoke_tool(launch=None, status=None, output=None):
    async def _invoke(tool_name, kwargs):
        if tool_name == "launch_job":
            return launch or {
                "success": True,
                "job_id": 42,
                "status": "pending",
                "template_name": "restart-pod",
            }
        if tool_name == "get_job_status":
            return status or {
                "success": True,
                "job_id": 42,
                "status": "successful",
                "elapsed": 5.0,
                "finished": "2024-01-01T00:01:00Z",
                "failed": False,
                "result_traceback": "",
            }
        if tool_name == "get_job_output":
            return output or {
                "success": True,
                "job_id": 42,
                "output": "PLAY OK",
            }
        return {}

    return _invoke


@pytest.mark.asyncio
class TestRemediateNode:
    async def test_success(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
        )
        with patch(
            "agent_service.nodes.remediate._invoke_tool",
            _mock_invoke_tool(),
        ):
            result = await node(state)

        assert result["remediation_result"].success is True
        assert result["remediation_result"].job_id == "42"
        assert result["remediation_result"].action_taken == "restart-pod"
        assert "should_retry" not in result

    async def test_launch_failure_sets_should_retry(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
        )
        mock = _mock_invoke_tool(
            launch={"success": False, "error": "template not found"}
        )
        with patch("agent_service.nodes.remediate._invoke_tool", mock):
            result = await node(state)

        assert result["remediation_result"].success is False
        assert result["should_retry"] is True
        assert len(result["failed_attempts"]) == 1
        assert result["failed_attempts"][0]["error"] == "template not found"

    async def test_job_failure_sets_should_retry(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
        )
        mock = _mock_invoke_tool(
            status={
                "success": True,
                "job_id": 42,
                "status": "failed",
                "elapsed": 3.0,
                "finished": "2024-01-01T00:01:00Z",
                "failed": True,
                "result_traceback": "task failed",
            }
        )
        with patch("agent_service.nodes.remediate._invoke_tool", mock):
            result = await node(state)

        assert result["remediation_result"].success is False
        assert result["should_retry"] is True
        assert len(result["failed_attempts"]) == 1

    async def test_timeout_does_not_retry(self):
        config = GraphConfig(job_timeout=0.1)
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
        )

        async def _slow_invoke(tool_name, kwargs):
            if tool_name == "launch_job":
                return {"success": True, "job_id": 99}
            if tool_name == "get_job_status":
                return {"success": True, "status": "running"}
            return {}

        with patch(
            "agent_service.nodes.remediate._invoke_tool", _slow_invoke
        ), patch("agent_service.nodes.remediate.POLL_INTERVAL_SECONDS", 0.01):
            result = await node(state)

        assert result["remediation_result"].success is False
        assert result["remediation_result"].timed_out is True
        assert "should_retry" not in result

    async def test_max_retries_exceeded_no_retry(self):
        config = GraphConfig(max_retries=1)
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
            failed_attempts=[{"action": "remediate", "template": "x", "error": "prev"}],
        )
        mock = _mock_invoke_tool(
            launch={"success": False, "error": "still broken"}
        )
        with patch("agent_service.nodes.remediate._invoke_tool", mock):
            result = await node(state)

        assert result["remediation_result"].success is False
        assert result["should_retry"] is False
        assert len(result["failed_attempts"]) == 2

    async def test_no_recommended_actions(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        rca = RootCauseAnalysis(
            failure_type="Unknown",
            confidence=0.9,
            summary="unknown issue",
            evidence=[],
            recommended_actions=[],
            estimated_severity="medium",
            runbook_reference="",
        )
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=rca,
        )
        with patch(
            "agent_service.nodes.remediate._invoke_tool",
            _mock_invoke_tool(),
        ):
            result = await node(state)

        assert result["remediation_result"].success is False
        assert result["should_retry"] is True
