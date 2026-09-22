"""Notify node — sends a Slack Block Kit message after remediation.

Follows the same pattern as agent-service nodes/notify.py (Workflow 1):
same slack_sdk, same Block Kit structure, same SLACK_ENABLED gate.
Fields are ML-schema RAN-specific (incident_id/zone/application) instead of pod/namespace.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.models.attachments import BlockAttachment
from slack_sdk.models.blocks import DividerBlock, HeaderBlock, SectionBlock

from ran_remediation_service.config import (
    SLACK_BOT_TOKEN,
    SLACK_CHANNEL,
    SLACK_ENABLED,
    SLACK_TIMEOUT_SECONDS,
)
from ran_remediation_service.models import RemediationState

_STATUS_COLORS = {
    True:  "#00AA00",  # green — success
    False: "#FF0000",  # red   — failure
}


def _build_payload(state: RemediationState) -> dict:
    status_label = "Completed" if state.success else "Failed"
    emoji = "✅" if state.success else "❌"
    title = f"[RAN Remediation {status_label}] {state.zone}/{state.application} — {state.incident_id}"

    blocks = [
        HeaderBlock(text=title[:150]),
        SectionBlock(
            fields=[
                f"*Incident ID:*\n{state.incident_id}",
                f"*Zone:*\n{state.zone}",
                f"*Application:*\n{state.application}",
                f"*AAP Template:*\n{state.template_name}",
                f"*Job ID:*\n{state.job_id or 'N/A'}",
                f"*Status:*\n{emoji} {state.job_status}",
            ]
        ),
    ]
    if state.root_cause:
        blocks += [DividerBlock(), SectionBlock(text=f"*Root cause:*\n{state.root_cause[:300]}")]
    if state.output_summary:
        blocks.append(SectionBlock(text=f"*Output:*\n{state.output_summary[:300]}"))

    attachment = BlockAttachment(
        blocks=blocks,
        color=_STATUS_COLORS[state.success],
        fallback=title,
    )
    return {
        "text": f"{emoji} RAN remediation {status_label.lower()}: {state.zone}/{state.application} ({state.incident_id})",
        "attachments": [attachment.to_dict()],
    }


async def notify_node(state: RemediationState) -> dict:
    if not (SLACK_ENABLED and SLACK_BOT_TOKEN):
        logger.info(
            "Slack disabled — remediation result: incident_id={} success={}",
            state.incident_id, state.success,
        )
        return {}

    try:
        payload = _build_payload(state)
    except Exception as exc:
        logger.warning("Slack payload build failed: {}", exc)
        return {}

    client = WebClient(token=SLACK_BOT_TOKEN, timeout=SLACK_TIMEOUT_SECONDS)
    try:
        await asyncio.to_thread(
            client.chat_postMessage,
            channel=SLACK_CHANNEL,
            **payload,
        )
        logger.info("Slack remediation notification sent incident_id={}", state.incident_id)
    except SlackApiError as exc:
        error = getattr(exc, "response", {}).get("error", str(exc))
        logger.warning("Slack notification failed: {}", error)

    return {}
