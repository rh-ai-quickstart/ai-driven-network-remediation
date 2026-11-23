"""Unit tests for the notify node."""

import runpy
from unittest.mock import patch

import pytest
from ran_remediation_service import config
from ran_remediation_service.models import RemediationState
from ran_remediation_service.nodes import notify
from ran_remediation_service.nodes.notify import _build_payload, notify_node


def _state(success: bool = True) -> RemediationState:
    return RemediationState(
        incident_id="a3f7c2d1",
        zone="A",
        application="File",
        ad_label="anomalous",
        ad_confidence=0.9995,
        root_cause="Signal degradation consistent with antenna misalignment.",
        recommended_fix="Adjust antenna tilt per vendor guide Section 4.3.2.",
        template_name="ran-antenna-tilt-adjust",
        job_id="7",
        job_status="successful" if success else "failed",
        success=success,
        output_summary="PLAY RECAP: ok=3 changed=2",
    )


def test_build_payload_success():
    payload = _build_payload(_state(success=True))
    assert "✅" in payload["text"]
    assert "Completed" in payload["text"] or "completed" in payload["text"].lower()
    assert "attachments" in payload


def test_build_payload_failure():
    payload = _build_payload(_state(success=False))
    assert "❌" in payload["text"]
    assert "Failed" in payload["text"] or "failed" in payload["text"].lower()


def test_build_payload_contains_incident_id():
    payload = _build_payload(_state())
    assert "a3f7c2d1" in str(payload)


def test_build_payload_contains_zone_and_application():
    payload = _build_payload(_state())
    content = str(payload)
    assert "Zone A" in content or "A" in content
    assert "File" in content


@pytest.mark.asyncio
async def test_notify_node_slack_disabled_returns_empty():
    state = _state()
    result = await notify_node(state)
    assert result == {}


@pytest.mark.asyncio
async def test_notify_node_slack_enabled_no_token_returns_empty():
    state = _state()
    with (
        patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", True),
        patch("ran_remediation_service.nodes.notify.SLACK_BOT_TOKEN", ""),
    ):
        result = await notify_node(state)
    assert result == {}


@pytest.mark.parametrize("enabled", [None, "false", "true"], ids=["default", "disabled", "enabled"])
@pytest.mark.asyncio
async def test_notify_node_uses_slack_settings_from_environment(monkeypatch, enabled):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-shared-test")
    monkeypatch.setenv("SLACK_CHANNEL", "#ran-team")
    if enabled is None:
        monkeypatch.delenv("SLACK_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SLACK_ENABLED", enabled)

    settings = runpy.run_path(config.__file__)
    with (
        patch.multiple(config, **{key: settings[key] for key in ("SLACK_ENABLED", "SLACK_BOT_TOKEN", "SLACK_CHANNEL")}),
        patch("slack_sdk.WebClient", autospec=True) as slack_client,
    ):
        node = runpy.run_path(notify.__file__)["notify_node"]
        assert await node(_state()) == {}

    if enabled == "true":
        assert slack_client.call_args.kwargs["token"] == "xoxb-shared-test"
        post_message = slack_client.return_value.chat_postMessage
        post_message.assert_called_once()
        assert post_message.call_args.kwargs["channel"] == "#ran-team"
        assert "a3f7c2d1" in post_message.call_args.kwargs["text"]
    else:
        slack_client.assert_not_called()
