import json
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


def _rag_stub(state: dict) -> dict:
    log_event = state.log_event
    query = f"{log_event.message} namespace={log_event.namespace} pod={log_event.pod_name}"
    return {"context_snippets": ["stub snippet"], "rag_query_used": query}


def _mock_invoke_tool(launch=None, status=None, output=None, upsert=None):
    async def _invoke(tool_name, kwargs):
        if tool_name == "upsert_job_template":
            return upsert or {
                "success": True,
                "template_id": 99,
                "created": True,
                "template_name": kwargs.get("template_name", ""),
                "playbook": kwargs.get("playbook", ""),
            }
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


async def _slow_invoke(tool_name, kwargs):
    if tool_name == "launch_job":
        return {"success": True, "job_id": 99}
    if tool_name == "get_job_status":
        return {"success": True, "status": "running"}
    return {}


_STUB_RCA = RootCauseAnalysis(
    failure_type="CrashLoopBackOff",
    confidence=0.9,
    summary="pod is crash-looping",
    evidence=["restart count > 5"],
    recommended_actions=["restart-pod"],
    estimated_severity="high",
    runbook_reference="runbook-001",
)

_OLS_STUB_RESPONSE = {
    "conversation_id": "stub-conv-id",
    "response": "- hosts: all\n  tasks: []",
}


_ols_mock = AsyncMock(return_value=_OLS_STUB_RESPONSE)

_OLS_STUB_RESPONSE = {
    "conversation_id": "stub-conv-id",
    "response": "- hosts: all\n  tasks: []",
}


_ols_mock = AsyncMock(return_value=_OLS_STUB_RESPONSE)


async def _mock_escalate_invoke(tool_name, kwargs):
    if tool_name == "create_incident":
        return {"success": True, "number": "INC0000001"}
    return {}


@pytest.fixture
def _patch_graph_nodes():
    _ols_mock.reset_mock()
    with (
        patch("agent_service.graph.rag_retrieval_node", _rag_stub),
        patch("agent_service.graph.analyze_node", _analyze_stub),
        patch("agent_service.nodes.remediate._invoke_tool", _mock_invoke_tool()),
        patch("agent_service.nodes.escalate._invoke_tool", _mock_escalate_invoke),
        patch("agent_service.nodes.lightspeed.LIGHTSPEED_URL", "http://ols-stub"),
        patch("agent_service.nodes.lightspeed._call_ols", _ols_mock),
        patch("agent_service.nodes.lightspeed._invoke_tool", _mock_invoke_tool()),
        patch("agent_service.nodes.audit.KafkaProducer"),
    ):
        yield


@pytest.fixture
def graph(_patch_graph_nodes):
    return build_graph()


class TestGraphCompilation:
    @pytest.fixture(autouse=True)
    def _compiled(self):
        g = build_graph().get_graph()
        self.nodes = {n.name for n in g.nodes.values()}
        self.edges = g.edges

    def test_expected_nodes_present(self):
        for name in ("remediate", "lightspeed", "audit"):
            assert name in self.nodes

    def test_audit_is_terminal_node_before_end(self):
        end_sources = [e.source for e in self.edges if e.target == "__end__"]
        assert end_sources == ["audit"]

    def test_notify_connects_to_audit_not_end(self):
        notify_targets = [e.target for e in self.edges if e.source == "notify"]
        assert "audit" in notify_targets
        assert "__end__" not in notify_targets


class TestNormalizeNode:
    async def test_normalize_extracts_canonical_json_fields(self, _patch_graph_nodes):
        canonical = json.dumps(
            {
                "@timestamp": "2024-01-15T10:30:00Z",
                "message": "nginx CrashLoopBackOff in namespace prod",
                "level": "error",
                "kubernetes": {
                    "namespace_name": "prod",
                    "pod_name": "nginx-abc123",
                    "container_name": "nginx",
                },
                "labels": {"edge_site_id": "edge-site-01"},
            }
        )
        graph = build_graph()
        result = await graph.ainvoke({"raw_event": canonical})
        log_event = result["log_event"]
        assert isinstance(log_event, LogEvent)
        assert log_event.timestamp == "2024-01-15T10:30:00Z"
        assert log_event.namespace == "prod"
        assert log_event.pod_name == "nginx-abc123"
        assert log_event.message == "nginx CrashLoopBackOff in namespace prod"


class TestRagRetrievalNode:
    async def test_rag_retrieval_sets_rag_query_used(self, _patch_graph_nodes):
        graph = build_graph()
        result = await graph.ainvoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})
        assert result["rag_query_used"] != ""

    async def test_rag_retrieval_sets_context_snippets(self, _patch_graph_nodes):
        graph = build_graph()
        result = await graph.ainvoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})
        assert len(result["context_snippets"]) > 0


class TestLinearFlow:
    async def test_end_to_end_produces_expected_state(self, graph):
        result = await graph.ainvoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})

        assert result["raw_event"] == "nginx CrashLoopBackOff in namespace prod"
        assert result["log_event"] is not None
        assert isinstance(result["log_event"], LogEvent)
        assert "nginx CrashLoopBackOff" in result["log_event"].message
        assert len(result["context_snippets"]) > 0
        assert result["rag_query_used"] != ""
        assert result["root_cause_analysis"] is not None
        assert isinstance(result["root_cause_analysis"], RootCauseAnalysis)
        assert isinstance(result["root_cause_analysis"].confidence, float)
        assert result["root_cause_analysis"].failure_type is not None
        assert result["decision"] != ""


class TestConditionalRouting:
    async def test_high_confidence_known_type_routes_through_remediate(self, graph):
        result = await graph.ainvoke({"raw_event": "test event"})

        assert result["decision"] == "remediate"
        assert isinstance(result["remediation_result"], RemediationResult)
        assert result["remediation_result"].success is True
        assert result["remediation_result"].generated_template_name is None

    async def test_high_confidence_generation_type_routes_through_lightspeed(self, graph):
        result = await graph.ainvoke(
            {
                "raw_event": "test event",
                "failure_type_override": "KafkaLag",
            }
        )

        assert result["decision"] == "lightspeed"
        assert isinstance(result["remediation_result"], RemediationResult)
        assert result["remediation_result"].generated_template_name is not None
        assert result["remediation_result"].generated_playbook_name is not None
        assert result["remediation_result"].job_id != ""

    async def test_low_confidence_routes_through_escalate(self, graph):
        result = await graph.ainvoke(
            {
                "raw_event": "test event",
                "confidence_override": 0.5,
            }
        )

        assert result["decision"] == "escalate"

    async def test_low_confidence_escalates_regardless_of_failure_type(self, graph):
        result = await graph.ainvoke(
            {
                "raw_event": "test event",
                "confidence_override": 0.5,
                "failure_type_override": "KafkaLag",
            }
        )

        assert result["decision"] == "escalate"
        assert result.get("remediation_result") is None

    async def test_custom_thresholds_alter_routing(self, _patch_graph_nodes):
        config = GraphConfig(remediate_threshold=0.9, escalate_threshold=0.8)
        graph = build_graph(config)
        result = await graph.ainvoke({"raw_event": "test event"})

        assert result["decision"] == "escalate"

    async def test_custom_thresholds_route_to_lightspeed(self, _patch_graph_nodes):
        config = GraphConfig(remediate_threshold=0.7, escalate_threshold=0.5)
        graph = build_graph(config)
        result = await graph.ainvoke(
            {
                "raw_event": "test event",
                "confidence_override": 0.75,
                "failure_type_override": "DNSFailure",
            }
        )

        assert result["decision"] == "lightspeed"
        assert result["remediation_result"].generated_template_name is not None
        assert result["remediation_result"].job_id != ""


@pytest.mark.usefixtures("_patch_graph_nodes")
class TestCli:
    def test_default_confidence_routes_to_remediate(self):
        runner = CliRunner()
        result = runner.invoke(main)

        assert result.exit_code == 0
        assert "next_action: remediate" in result.output
        assert "rca:" in result.output
        assert "incident_id:" in result.output

    def test_low_confidence_routes_to_escalate(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--confidence", "0.5"])

        assert result.exit_code == 0
        assert "next_action: escalate" in result.output

    def test_lightspeed_route_via_failure_type(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--failure-type", "KafkaLag"])

        assert result.exit_code == 0
        assert "next_action: lightspeed" in result.output


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
        assert result["remediation_result"].action_taken == "restart-nginx"
        assert result["should_retry"] is False

    async def test_success_after_failure_clears_should_retry(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
            failed_attempts=[
                {"action": "remediate", "template": "x", "error": "prev"},
            ],
            should_retry=True,
        )
        with patch(
            "agent_service.nodes.remediate._invoke_tool",
            _mock_invoke_tool(),
        ):
            result = await node(state)

        assert result["remediation_result"].success is True
        assert result["should_retry"] is False

    async def test_launch_failure_sets_should_retry(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
        )
        mock = _mock_invoke_tool(launch={"success": False, "error": "template not found"})
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
        with (
            patch("agent_service.nodes.remediate._invoke_tool", _slow_invoke),
            patch("agent_service.nodes.remediate.POLL_INTERVAL_SECONDS", 0.01),
        ):
            result = await node(state)

        assert result["remediation_result"].success is False
        assert result["remediation_result"].timed_out is True
        assert result["should_retry"] is True
        assert len(result["failed_attempts"]) == 1

    async def test_failure_then_timeout_stops_retrying(self):
        config = GraphConfig(max_retries=1, job_timeout=0.1)
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
            failed_attempts=[
                {"action": "remediate", "template": "x", "error": "prev"},
            ],
            should_retry=True,
        )
        with (
            patch("agent_service.nodes.remediate._invoke_tool", _slow_invoke),
            patch("agent_service.nodes.remediate.POLL_INTERVAL_SECONDS", 0.01),
        ):
            result = await node(state)

        assert result["remediation_result"].success is False
        assert result["remediation_result"].timed_out is True
        assert result["should_retry"] is False
        assert len(result["failed_attempts"]) == 2

    async def test_max_retries_exceeded_no_retry(self):
        config = GraphConfig(max_retries=1)
        node = make_remediate_node(config)
        state = IncidentState(
            raw_event="test",
            log_event=_STUB_LOG_EVENT,
            root_cause_analysis=_STUB_RCA,
            failed_attempts=[{"action": "remediate", "template": "x", "error": "prev"}],
        )
        mock = _mock_invoke_tool(launch={"success": False, "error": "still broken"})
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
        assert result["should_retry"] is False
        assert "failed_attempts" not in result
