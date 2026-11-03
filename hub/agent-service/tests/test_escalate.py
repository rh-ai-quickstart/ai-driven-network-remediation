from unittest.mock import patch

from helpers import make_state

from agent_service.models import RemediationResult, RootCauseAnalysis
from agent_service.nodes.escalate import escalate_node
from agent_service.nodes.remediate import _failure


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


def _stub_remediation(**overrides):
    defaults = dict(
        action_taken="generate-playbook",
        tool_used="lightspeed",
        success=False,
        job_id="conversation-1",
        duration_seconds=1.0,
        output_summary="Generated playbook failed validation: invalid task",
        timestamp="2024-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return RemediationResult(**defaults)


_FAKE_URL = "https://dev.service-now.com/incident.do?sys_id=abc123"


def _make_capture_invoke(number="INC0012345"):
    captured = {}

    async def _capture_invoke(tool_name, kwargs):
        captured.update({"tool_name": tool_name, "kwargs": kwargs})
        return {"success": True, "ticket_number": number, "incident_url": _FAKE_URL}

    return captured, _capture_invoke


async def _fake_invoke(tool_name, kwargs):
    if tool_name == "create_incident":
        return {"success": True, "ticket_number": "INC0012345", "incident_url": _FAKE_URL}
    return {}


class TestEscalateHappyPath:
    async def test_creates_servicenow_ticket(self):
        state = make_state(root_cause_analysis=_stub_rca())
        with patch("agent_service.nodes.escalate._invoke_tool", _fake_invoke):
            result = await escalate_node(state)

        assert result["servicenow_ticket"] == "INC0012345"

    async def test_reads_ticket_number_key_not_number(self):
        state = make_state(root_cause_analysis=_stub_rca())

        async def _legacy_key_invoke(tool_name, kwargs):
            return {"success": True, "number": "INC0012345"}

        with patch("agent_service.nodes.escalate._invoke_tool", _legacy_key_invoke):
            result = await escalate_node(state)

        assert result["servicenow_ticket"] == ""

    async def test_logs_ticket_number_and_url(self):
        state = make_state(root_cause_analysis=_stub_rca())

        with (
            patch("agent_service.nodes.escalate._invoke_tool", _fake_invoke),
            patch("agent_service.nodes.escalate.logger") as mock_logger,
        ):
            await escalate_node(state)

        logged = mock_logger.info.call_args[0][0]
        assert "INC0012345" in logged
        assert _FAKE_URL in logged

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


class TestEscalateRemediationResult:
    async def test_failed_lightspeed_result_includes_validation_error(self):
        state = make_state(
            root_cause_analysis=_stub_rca(),
            remediation_result=_stub_remediation(),
        )
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        desc = captured["kwargs"]["description"]
        assert "--- Failed Remediation Result ---" in desc
        assert "Tool: lightspeed" in desc
        assert "Action: generate-playbook" in desc
        assert "Job ID:" not in desc
        assert "Output: Generated playbook failed validation: invalid task" in desc

    async def test_failed_aap_result_includes_job_id(self):
        state = make_state(
            root_cause_analysis=_stub_rca(),
            remediation_result=_stub_remediation(
                tool_used="aap",
                action_taken="remediate-oomkilled-nginx",
                job_id="42",
                output_summary="Job 42 failed: fatal task error",
            ),
        )
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        description = captured["kwargs"]["description"]
        assert "Tool: aap" in description
        assert "Action: remediate-oomkilled-nginx" in description
        assert "Job ID: 42" in description
        assert "Output: Job 42 failed: fatal task error" in description

    async def test_failed_aap_result_without_job_id_omits_job_line(self):
        state = make_state(
            root_cause_analysis=_stub_rca(),
            remediation_result=_stub_remediation(
                tool_used="aap",
                action_taken="restart",
                job_id="",
            ),
        )
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        description = captured["kwargs"]["description"]
        assert "Tool: aap" in description
        assert "Action: restart" in description
        assert "Job ID:" not in description

    async def test_failure_over_500_characters_appears_once_with_tail(self):
        failure_prefix = "AAP failure prefix:"
        failure = failure_prefix + " x" * 300 + " retained-tail: task module traceback"
        state = make_state(root_cause_analysis=_stub_rca())
        state = state.model_copy(update=_failure("restart", failure, job_id=42))
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        description = captured["kwargs"]["description"]
        assert description.count(failure_prefix) == 1
        assert "retained-tail: task module traceback" in description
        assert "Tool: aap" in description
        assert "Action: restart" in description
        assert "Job ID: 42" in description

    async def test_successful_remediation_result_is_omitted(self):
        state = make_state(
            root_cause_analysis=_stub_rca(),
            remediation_result=_stub_remediation(
                success=True,
                output_summary="Generated playbook: fix-crashloop",
            ),
        )
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        desc = captured["kwargs"]["description"]
        assert "Failed Remediation Result" not in desc
        assert "Generated playbook: fix-crashloop" not in desc

    async def test_plain_escalation_omits_failure_sections(self):
        state = make_state(root_cause_analysis=_stub_rca())
        captured, capture_invoke = _make_capture_invoke()

        with patch("agent_service.nodes.escalate._invoke_tool", capture_invoke):
            await escalate_node(state)

        desc = captured["kwargs"]["description"]
        assert "Failed Remediation Result" not in desc
