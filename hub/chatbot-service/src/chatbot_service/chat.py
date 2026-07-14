"""LLM chat: context building, model calls, and reply formatting."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import (
    MODEL_API_URL,
    MODEL_MAX_TOKENS,
    MODEL_NAME,
    MODEL_TIMEOUT_SECONDS,
    SSL_VERIFY,
)
from .utils import get_mcp_items

logger = logging.getLogger(__name__)


def _format_recent_incidents(integrations_data: dict[str, Any]) -> str:
    """Extract recent incident-audit events for LLM context."""
    movie = integrations_data.get("incident_movie", [])
    if not movie:
        return "No recent remediation events."
    lines = []
    for ev in movie[:5]:
        lines.append(f"  - [{ev.get('stage')}] {ev.get('title')}: {ev.get('summary', '')[:200]}")
    return "\n".join(lines)


def _format_slo_context(integrations_data: dict[str, Any]) -> str:
    """Extract SLO and business impact metrics for LLM context."""
    slo = integrations_data.get("slo", {})
    bi = integrations_data.get("business_impact", {})
    if not slo and not bi:
        return "No SLO data available yet."

    confidence = bi.get("model_confidence_avg")
    if confidence is not None:
        conf_str = f"{confidence * 100:.0f}%" if confidence <= 1 else f"{confidence:.0f}%"
    else:
        conf_str = "n/a"

    return (
        f"  - Auto-remediation rate: {slo.get('auto_remediation_pct', 0):.0f}%\n"
        f"  - AI model confidence: {conf_str}\n"
        f"  - MTTR: {slo.get('mttr_seconds', 'n/a')}s\n"
        f"  - Incidents processed: {bi.get('incidents_processed', 0)}\n"
        f"  - Remediation success: {bi.get('remediation_success_pct', 0):.0f}%"
    )


def build_chat_context(
    user_message: str,
    summary_data: dict[str, Any],
    integrations_data: dict[str, Any],
    history: list[dict[str, str]],
) -> str:
    """Build a context-rich prompt for the LLM."""
    mcp_items = get_mcp_items(integrations_data)
    mcp_line = ", ".join(f"{i['name']}={i['status']}" for i in mcp_items) or "no-mcp-data"
    recent = history[-4:]
    convo = "\n".join(f"{item['role']}: {item['content']}" for item in recent) or "none"
    incidents_context = _format_recent_incidents(integrations_data)
    slo_context = _format_slo_context(integrations_data)

    return (
        "You are the NOC assistant for an AI-driven network remediation system.\n"
        "Answer the user's request directly with concise, actionable analysis.\n"
        "When describing remediation, explain the FULL workflow:\n"
        "  1. Event detection via Kafka\n"
        "  2. RAG retrieval from runbook knowledge base\n"
        "  3. LLM root-cause analysis (mention confidence %)\n"
        "  4. Autonomous decision to remediate\n"
        "  5. AAP job execution (mention template name and result)\n"
        "Include specific metrics (confidence, MTTR, success rate) from the data below.\n"
        "Do NOT repeat headers or formatting — just provide your insight.\n"
        "Keep output under 250 words.\n\n"
        f"Current state:\n"
        f"- Model: {MODEL_NAME}\n"
        f"- Site: {summary_data.get('site')} | Cluster: {summary_data.get('cluster')}\n"
        f"- Integrations up/total: {integrations_data.get('up')}/{integrations_data.get('total')}\n"
        f"- MCP status: {mcp_line}\n\n"
        f"Remediation metrics:\n{slo_context}\n\n"
        f"Recent remediation activity:\n{incidents_context}\n\n"
        f"Recent conversation: {convo}\n\n"
        f"User request: {user_message}\n\n"
        "Your analysis:"
    )


async def call_model(prompt: str) -> tuple[str, str]:
    """Call the LLM endpoint. Returns (reply_text, source).

    NOTE: Minimal implementation sufficient for V1 (single vLLM endpoint).
    Consider replacing with litellm/llama-index if we need streaming,
    multi-model fallback, or token management.
    """
    if not MODEL_API_URL:
        return "", "disabled"
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "max_tokens": MODEL_MAX_TOKENS,
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=MODEL_TIMEOUT_SECONDS, verify=SSL_VERIFY) as client:
            resp = await client.post(MODEL_API_URL, json=payload)
        if resp.status_code != 200:
            logger.warning("LLM returned HTTP %d from %s", resp.status_code, MODEL_API_URL)
            return "", f"http-{resp.status_code}"
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            text = (choices[0].get("text") or choices[0].get("message", {}).get("content") or "").strip()
            if text:
                logger.debug("LLM replied with %d chars", len(text))
                return text, "live"
        return "", "empty"
    except Exception:
        logger.warning("LLM unreachable at %s", MODEL_API_URL, exc_info=True)
        return "", "unreachable"


def format_chat_reply(
    user_message: str,
    raw_reply: str,
    summary_data: dict[str, Any],
    integrations_data: dict[str, Any],
) -> str:
    """Format LLM output into structured executive reply, or generate fallback."""
    mcp_items = get_mcp_items(integrations_data)
    mcp_lines = [f"- {i['name']}: {i['status']} (http={i.get('http_code') or 'n/a'})" for i in mcp_items]
    if not mcp_lines:
        mcp_lines = ["- No MCP status available."]

    down_agents = [i["name"] for i in mcp_items if i.get("status") != "up"]

    up = integrations_data.get("up", 0)
    total = integrations_data.get("total", 0)

    movie = integrations_data.get("incident_movie", [])
    if movie:
        latest = movie[0]
        status_line = f"- Latest incident: {latest.get('title')} → {latest.get('stage')}"
        if latest.get("summary"):
            status_line += f"\n- Detail: {latest['summary'][:200]}"
    else:
        status_line = "- No recent remediation events."

    if raw_reply:
        model_insight = raw_reply.strip()
    else:
        model_insight = "Live model unavailable; using deterministic operational fallback."

    if down_agents:
        action_1 = f"Recover unhealthy MCP agents: {', '.join(down_agents)}."
    elif movie and movie[0].get("stage") == "Auto-Remediated":
        action_1 = "Remediation successful — verify workload stability and close ticket."
    else:
        action_1 = "All MCP agents healthy; continue monitoring."

    return (
        "Summary:\n"
        f"- Integrations: {up}/{total} up\n"
        f"{status_line}\n"
        f"- Request: {user_message}\n\n"
        "MCP Status:\n" + "\n".join(mcp_lines) + "\n\n"
        "Model Output:\n"
        f"- {model_insight}\n\n"
        "Next Action:\n"
        f"1. {action_1}\n"
        "2. Confirm ServiceNow ticket update and Slack notification.\n"
        "3. Validate edge workload recovery and Kafka telemetry continuity."
    )
