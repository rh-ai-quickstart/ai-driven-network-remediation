from unittest.mock import patch

from click.testing import CliRunner

from agent_service import main
from agent_service.graph import build_graph
from agent_service.models import GraphConfig, RootCauseAnalysis


def _make_analyze_stub(confidence: float):
    def analyze_node(state: dict) -> dict:
        rca = RootCauseAnalysis(
            root_cause="stub",
            confidence=confidence,
            severity="medium",
            affected_components=["test"],
            recommended_playbook="test-playbook",
            reasoning="stub reasoning",
        )
        return {"root_cause_analysis": rca}

    return analyze_node


class TestGraphCompilation:
    def test_graph_compiles(self):
        graph = build_graph()
        assert graph is not None


class TestLinearFlow:
    def test_end_to_end_produces_expected_state(self):
        graph = build_graph()
        result = graph.invoke({"raw_event": "nginx CrashLoopBackOff in namespace prod"})

        assert result["raw_event"] == "nginx CrashLoopBackOff in namespace prod"
        assert len(result["context_snippets"]) > 0
        assert result["root_cause_analysis"] is not None
        assert isinstance(result["root_cause_analysis"], RootCauseAnalysis)
        assert isinstance(result["root_cause_analysis"].confidence, float)
        assert result["decision"] != ""
        assert len(result["notifications_sent"]) > 0


class TestConditionalRouting:
    def test_high_confidence_routes_through_execute(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.85)):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "execute"
        assert result["execution_result"] != ""
        assert len(result["notifications_sent"]) > 0

    def test_low_confidence_routes_through_escalate(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.5)):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "escalate"
        assert len(result["notifications_sent"]) > 0

    def test_mid_confidence_routes_through_request_approval(self):
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.75)):
            graph = build_graph()
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "request_approval"
        assert result["awaiting_human_approval"] is True
        assert len(result["notifications_sent"]) > 0

    def test_custom_thresholds_alter_routing(self):
        config = GraphConfig(remediate_threshold=0.9, escalate_threshold=0.8)
        with patch("agent_service.graph.analyze_node", _make_analyze_stub(0.85)):
            graph = build_graph(config)
            result = graph.invoke({"raw_event": "test event"})

        assert result["decision"] == "request_approval"


class TestConfidenceOverride:
    def test_confidence_override_controls_routing(self):
        graph = build_graph()
        result = graph.invoke({"raw_event": "test event", "confidence_override": 0.5})

        assert result["root_cause_analysis"].confidence == 0.5
        assert result["decision"] == "escalate"


class TestCli:
    def test_default_confidence_routes_to_execute(self):
        runner = CliRunner()
        result = runner.invoke(main)

        assert result.exit_code == 0
        assert "'decision': 'execute'" in result.output

    def test_low_confidence_routes_to_escalate(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--confidence", "0.5"])

        assert result.exit_code == 0
        assert "'decision': 'escalate'" in result.output

    def test_mid_confidence_routes_to_request_approval(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--confidence", "0.75"])

        assert result.exit_code == 0
        assert "'decision': 'request_approval'" in result.output
