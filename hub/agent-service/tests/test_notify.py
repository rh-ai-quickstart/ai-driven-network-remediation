import json
from unittest.mock import AsyncMock, patch

from helpers import make_rca, make_state

from agent_service.models import RemediationResult
from agent_service.nodes.notify import _build_payload, notify_node


def _stub_remediation(**overrides):
    defaults = dict(
        action_taken="restart-pod",
        tool_used="aap",
        success=True,
        job_id="42",
        duration_seconds=12.5,
        output_summary="Pod restarted successfully",
        timestamp="2024-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return RemediationResult(**defaults)


def _notify_state(**overrides):
    defaults = dict(
        root_cause_analysis=make_rca(),
        decision="remediate",
        remediation_result=_stub_remediation(),
    )
    defaults.update(overrides)
    return make_state(**defaults)


def _payload_text(payload):
    return json.dumps(payload)


_SLACK_PATCHES = {
    "SLACK_ENABLED": True,
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_CHANNEL": "#test",
}


class TestBuildPayload:
    def test_remediation_success(self):
        state = _notify_state()
        payload = _build_payload(state)
        dump = _payload_text(payload)

        assert "HIGH" in dump
        assert "OOMKilled" in dump
        assert "prod/nginx-abc123" in dump
        assert "edge-1" in dump
        assert "Remediated via aap (job 42)" in dump
        assert payload["attachments"][0]["color"] == "#FF6600"

    def test_remediation_failure(self):
        state = _notify_state(
            remediation_result=_stub_remediation(success=False, output_summary="timeout"),
        )
        dump = _payload_text(_build_payload(state))

        assert "Remediation failed: timeout" in dump

    def test_lightspeed(self):
        state = _notify_state(
            decision="lightspeed",
            remediation_result=_stub_remediation(
                tool_used="lightspeed",
                output_summary="Generated playbook: fix-oom",
            ),
        )
        dump = _payload_text(_build_payload(state))

        assert "Lightspeed playbook generated" in dump

    def test_escalate(self):
        state = _notify_state(decision="escalate", remediation_result=None)
        dump = _payload_text(_build_payload(state))

        assert "Escalated to ServiceNow" in dump

    def test_missing_rca_does_not_crash(self):
        state = make_state(decision="escalate")
        dump = _payload_text(_build_payload(state))

        assert "UNKNOWN" in dump

    def test_severity_color_critical(self):
        state = _notify_state(
            root_cause_analysis=make_rca(estimated_severity="critical"),
            decision="escalate",
            remediation_result=None,
        )
        payload = _build_payload(state)

        assert payload["attachments"][0]["color"] == "#FF0000"

    def test_ticket_link_with_servicenow_url(self):
        state = _notify_state(
            decision="escalate",
            remediation_result=None,
            servicenow_ticket="INC0012345",
        )
        with patch(
            "agent_service.nodes.notify.SERVICENOW_INSTANCE_URL",
            "https://snow.example.com",
        ):
            dump = _payload_text(_build_payload(state))

        assert "INC0012345" in dump
        assert "https://snow.example.com/nav_to.do" in dump

    def test_long_title_truncated(self):
        long_ns = "a" * 63
        long_pod = "b" * 253
        state = _notify_state(
            root_cause_analysis=make_rca(),
            decision="escalate",
            remediation_result=None,
        )
        state.log_event = state.log_event.model_copy(update={"namespace": long_ns, "pod_name": long_pod})
        payload = _build_payload(state)
        title = payload["attachments"][0]["blocks"][0]["text"]["text"]
        assert len(title) <= 150

    def test_ticket_link_without_servicenow_url(self):
        state = _notify_state(
            decision="escalate",
            remediation_result=None,
            servicenow_ticket="INC0012345",
        )
        with patch("agent_service.nodes.notify.SERVICENOW_INSTANCE_URL", ""):
            dump = _payload_text(_build_payload(state))

        assert "Ticket" not in dump


class TestNotifyNodeDisabled:
    async def test_disabled_returns_empty_ts_and_logs(self):
        state = _notify_state(remediation_result=None)
        with (
            patch("agent_service.nodes.notify.SLACK_ENABLED", False),
            patch("agent_service.nodes.notify.logger") as mock_logger,
        ):
            result = await notify_node(state)

        assert result == {"slack_thread_ts": ""}
        mock_logger.info.assert_called_once()
        assert "Slack disabled" in str(mock_logger.info.call_args)


class TestNotifyNodeHappyPath:
    async def test_sends_and_returns_ts(self):
        state = _notify_state()
        fake_send = AsyncMock(return_value="1234567890.123456")

        with (
            patch.multiple("agent_service.nodes.notify", **_SLACK_PATCHES),
            patch("agent_service.nodes.notify._send_slack_message", fake_send),
        ):
            result = await notify_node(state)

        assert result == {"slack_thread_ts": "1234567890.123456"}
        fake_send.assert_awaited_once()


class TestNotifyNodeError:
    async def test_slack_failure_returns_empty_ts_and_logs(self):
        state = _notify_state()
        fake_send = AsyncMock(side_effect=ConnectionError("unreachable"))

        with (
            patch.multiple("agent_service.nodes.notify", **_SLACK_PATCHES),
            patch("agent_service.nodes.notify._send_slack_message", fake_send),
            patch("agent_service.nodes.notify.logger") as mock_logger,
        ):
            result = await notify_node(state)

        assert result == {"slack_thread_ts": ""}
        mock_logger.warning.assert_called_once()
        assert "unreachable" in str(mock_logger.warning.call_args)
