import asyncio
from urllib.parse import quote

from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.models.attachments import BlockAttachment
from slack_sdk.models.blocks import DividerBlock, HeaderBlock, SectionBlock

from agent_service.config import (
    SERVICENOW_INSTANCE_URL,
    SLACK_BOT_TOKEN,
    SLACK_CHANNEL,
    SLACK_ENABLED,
    SLACK_TIMEOUT_SECONDS,
    now_iso,
)

# Block Kit attachment sidebar colors per severity
_SEVERITY_COLORS = {
    "critical": "#FF0000",
    "high": "#FF6600",
    "medium": "#FFAA00",
    "low": "#00AA00",
    "info": "#0066CC",
}

_STATUS_EMOJIS = {
    "Resolved": "✅",
    "Failed": "❌",
    "Escalated": "🔺",
}


def _build_title(rca, log_event, severity: str) -> str:
    if not log_event:
        return "[UNKNOWN] Incident Detected"[:150]
    prefix = f"[{severity.upper()}] {rca.failure_type}" if rca else "[UNKNOWN] Incident"
    return f"{prefix} - {log_event.namespace}/{log_event.pod_name}"[:150]


def _resolve_status(decision, rem) -> tuple[str, str]:
    if decision == "remediate" and rem:
        if rem.success:
            return (
                "Resolved",
                f"Remediated via {rem.tool_used} (job {rem.job_id})",
            )
        return "Failed", f"Remediation failed: {rem.output_summary}"
    if decision == "lightspeed" and rem:
        return (
            "Playbook Generated",
            f"Lightspeed playbook generated: {rem.output_summary}",
        )
    if decision == "escalate":
        return "Escalated", "Escalated to ServiceNow"
    return decision or "unknown", f"Decision: {decision or 'none'}"


def _build_payload(state) -> dict:
    """Build Slack Block Kit payload with colored attachment."""
    rca = state.root_cause_analysis
    log_event = state.log_event

    # Derive display values from graph state
    severity = rca.estimated_severity.lower() if rca else "info"
    title = _build_title(rca, log_event, severity)
    status, resolution = _resolve_status(
        state.decision,
        state.remediation_result,
    )
    summary = f"{title} - {resolution}"
    description = (rca and rca.summary) or (log_event and log_event.message) or ""
    emoji = _STATUS_EMOJIS.get(status, "")
    resolution_line = f"{emoji} {resolution}" if emoji else resolution
    site = log_event.edge_site_id if log_event else "N/A"
    timestamp = now_iso()

    # Assemble Block Kit blocks
    blocks = [
        HeaderBlock(text=title),
        SectionBlock(
            fields=[
                f"*Severity:*\n{severity.upper()}",
                f"*Site:*\n{site}",
                f"*Time:*\n{timestamp}",
                f"*Status:*\n{status}",
            ]
        ),
    ]
    if description:
        blocks += [DividerBlock(), SectionBlock(text=f"*Description:*\n{description}")]
    blocks += [DividerBlock(), SectionBlock(text=f"*Resolution:*\n{resolution_line}")]

    # Optional ServiceNow ticket link
    if state.servicenow_ticket and SERVICENOW_INSTANCE_URL:
        ticket = state.servicenow_ticket
        target = f"incident.do?sysparm_query=number={ticket}"
        url = f"{SERVICENOW_INSTANCE_URL}/nav_to.do?uri={quote(target, safe='')}"
        blocks.append(SectionBlock(text=f"*Ticket:* <{url}|{ticket}>"))

    # Wrap blocks in colored attachment
    attachment = BlockAttachment(
        blocks=blocks,
        color=_SEVERITY_COLORS.get(severity, _SEVERITY_COLORS["info"]),
        fallback=summary,
    )
    return {"text": summary, "attachments": [attachment.to_dict()]}


async def _send_slack_message(payload: dict) -> str:
    """Post payload to Slack; sync client wrapped in to_thread."""
    client = WebClient(
        token=SLACK_BOT_TOKEN,
        timeout=SLACK_TIMEOUT_SECONDS,
    )
    try:
        response = await asyncio.to_thread(
            client.chat_postMessage,
            channel=SLACK_CHANNEL,
            **payload,
        )
        return response.get("ts", "")
    except SlackApiError as exc:
        error = getattr(exc, "response", {}).get("error", str(exc))
        logger.warning(f"Slack API error: {error}")
        return ""


async def notify_node(state) -> dict:
    """Graph node: send Slack notification, return thread timestamp."""
    try:
        payload = _build_payload(state)
    except Exception as exc:
        logger.warning(f"Slack payload build failed: {exc}")
        return {"slack_thread_ts": ""}

    if not (SLACK_ENABLED and SLACK_BOT_TOKEN):
        logger.info(f"Slack disabled or token not set, fallback: {payload['text']}")
        return {"slack_thread_ts": ""}

    try:
        ts = await _send_slack_message(payload)
    except Exception as exc:
        logger.warning(f"Slack notification failed: {exc}")
        return {"slack_thread_ts": ""}

    if not ts:
        return {"slack_thread_ts": ""}

    logger.info(f"Slack message sent, ts={ts}")
    return {"slack_thread_ts": ts}
