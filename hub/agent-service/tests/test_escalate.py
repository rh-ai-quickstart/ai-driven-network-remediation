from unittest.mock import patch

from agent_service.models import RootCauseAnalysis
from agent_service.nodes.escalate import escalate_node
from helpers import make_state


def _stub_rca(**overrides):
    defaults = dict(
        failure_type="CrashLoopBackOff",
        confidence=0.5,
        summary="pod is crash-looping",
        evidence=["restart count > 5"],
        recommended_actions=["restart-pod"],
        estimated_severity="high",
        runbook_reference="runbook-001",
    )
    defaults.update(overrides)
    return RootCauseAnalysis(**defaults)


def _make_capture_invoke(number="INC0012345"):
    captured = {}

    async def _capture_invoke(tool_name, kwargs):
        captured.update({"tool_name": tool_name, "kwargs": kwargs})
        return {"success": True, "number": number}

    return captured, _capture_invoke


async def _fake_invoke(tool_name, kwargs):
    if tool_name == "create_incident":
        return {"success": True, "number": "INC0012345"}
    return {}


class TestEscalateHappyPath:
    async def test_creates_servicenow_ticket(self):
        state = make_state(root_cause_analysis=_stub_rca())
        with patch("agent_service.nodes.escalate._invoke_tool", _fake_invoke):
            result = await escalate_node(state)

        assert result["servicenow_ticket"] == "INC0012345"

    async def test_does_not_set_decision(self):
        state = make_state(root_cause_analysis=_stub_rca())
        with patch("agent_service.nodes.escalate._invoke_tool", _fake_invoke):
            result = await escalate_node(state)

        assert "decision" not in result

    async def test_calls_create_incident_with_correct_short_description(self):
        state = make_state(root_cause_analysis=_stub_rca())
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        assert captured["tool_name"] == "create_incident"
        expected_desc = "[AI-NOC] CrashLoopBackOff – nginx-abc123 in prod (edge-1)"
        assert captured["kwargs"]["short_description"] == expected_desc

    async def test_description_contains_rca_context(self):
        state = make_state(root_cause_analysis=_stub_rca())
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        desc = captured["kwargs"]["description"]
        assert "CrashLoopBackOff" in desc
        assert "pod is crash-looping" in desc
        assert "restart count > 5" in desc
        assert "restart-pod" in desc
        assert "CrashLoopBackOff" in desc


class TestPriorityMapping:
    async def _get_priority(self, severity):
        state = make_state(root_cause_analysis=_stub_rca(estimated_severity=severity))
        captured, capture_invoke = _make_capture_invoke(number="INC0099")

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        return captured["kwargs"]["priority"]

    async def test_critical_maps_to_1(self):
        assert await self._get_priority("critical") == 1

    async def test_high_maps_to_2(self):
        assert await self._get_priority("high") == 2

    async def test_medium_maps_to_3(self):
        assert await self._get_priority("medium") == 3

    async def test_low_maps_to_4(self):
        assert await self._get_priority("low") == 4


class TestEscalateErrorHandling:
    async def test_servicenow_failure_returns_empty_ticket(self):
        state = make_state(root_cause_analysis=_stub_rca())

        async def _fail_invoke(tool_name, kwargs):
            return {"success": False, "error": "connection refused"}

        with patch("agent_service.nodes.escalate._invoke_tool", _fail_invoke):
            result = await escalate_node(state)

        assert result["servicenow_ticket"] == ""
        assert result["error_message"] == "connection refused"

    async def test_servicenow_exception_returns_empty_ticket(self):
        state = make_state(root_cause_analysis=_stub_rca())

        async def _explode_invoke(tool_name, kwargs):
            raise ConnectionError("MCP server unreachable")

        with patch("agent_service.nodes.escalate._invoke_tool", _explode_invoke):
            result = await escalate_node(state)

        assert result["servicenow_ticket"] == ""
        assert "MCP server unreachable" in result["error_message"]

    async def test_servicenow_failure_logs_warning(self):
        state = make_state(root_cause_analysis=_stub_rca())

        async def _fail_invoke(tool_name, kwargs):
            return {"success": False, "error": "connection refused"}

        with (
            patch("agent_service.nodes.escalate._invoke_tool", _fail_invoke),
            patch("agent_service.nodes.escalate.logger") as mock_logger,
        ):
            await escalate_node(state)

        mock_logger.warning.assert_called_once()
        assert "connection refused" in mock_logger.warning.call_args[0][0]


class TestEscalateFailedAttempts:
    async def test_failed_attempts_appear_in_description(self):
        attempts = [
            {"action": "remediate", "template": "restart_deployment", "error": "timeout after 120s", "job_id": 42},
            {"action": "remediate", "template": "scale_up", "error": "quota exceeded", "job_id": 99},
        ]
        state = make_state(
            root_cause_analysis=_stub_rca(),
            failed_attempts=attempts,
        )
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        desc = captured["kwargs"]["description"]
        assert "--- Failed Remediation Attempts ---" in desc
        assert "1. Template: restart_deployment | Job: 42 | Error: timeout after 120s" in desc
        assert "2. Template: scale_up | Job: 99 | Error: quota exceeded" in desc

    async def test_empty_failed_attempts_omits_section(self):
        state = make_state(root_cause_analysis=_stub_rca(), failed_attempts=[])
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        desc = captured["kwargs"]["description"]
        assert "Failed Remediation Attempts" not in desc

    async def test_missing_job_id_omits_job_field(self):
        attempts = [
            {"action": "remediate", "template": "restart_deployment", "error": "timeout after 120s"},
        ]
        state = make_state(
            root_cause_analysis=_stub_rca(),
            failed_attempts=attempts,
        )
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        desc = captured["kwargs"]["description"]
        assert "1. Template: restart_deployment | Error: timeout after 120s" in desc
        assert "Job:" not in desc
