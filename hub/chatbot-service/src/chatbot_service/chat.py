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


def build_chat_context(
    user_message: str,
    summary_data: dict[str, Any],
    integrations_data: dict[str, Any],
    history: list[dict[str, str]],
) -> str:
    """Build a context-rich prompt for the LLM.

    TODO: Consider making the system prompt configurable (env var or file)
    to allow prompt iteration without code changes.
    """
    mcp_items = get_mcp_items(integrations_data)
    mcp_line = ", ".join(f"{i['name']}={i['status']}" for i in mcp_items) or "no-mcp-data"
    recent = history[-4:]
    convo = "\n".join(f"{item['role']}: {item['content']}" for item in recent) or "none"

    return (
        "You are the NOC assistant for an AI-driven network remediation system.\n"
        "Return concise, actionable text. Use headers: Summary, MCP Status, Model Output, Next Action.\n"
        "Keep output under 140 words.\n"
        f"Model: {MODEL_NAME}\n"
        f"Site: {summary_data.get('site')} | Cluster: {summary_data.get('cluster')}\n"
        f"Open incidents: {summary_data.get('open_incidents')}\n"
        f"Integrations up/total: {integrations_data.get('up')}/{integrations_data.get('total')}\n"
        f"MCP status: {mcp_line}\n"
        f"Recent conversation: {convo}\n"
        f"User request: {user_message}"
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
    if down_agents:
        action_1 = f"Recover unhealthy MCP agents: {', '.join(down_agents)}."
    else:
        action_1 = "All MCP agents healthy; proceed with remediation workflow."

    site = summary_data.get("site", "edge-01")
    incidents = summary_data.get("open_incidents", 0)
    up = integrations_data.get("up", 0)
    total = integrations_data.get("total", 0)

    if raw_reply:
        lines = [line.strip() for line in raw_reply.splitlines() if line.strip()]
        model_insight = lines[0][:220] if lines else "Model inference completed."
    else:
        model_insight = "Live model unavailable; using deterministic operational fallback."

    return (
        "Summary:\n"
        f"- Site: {site} | Open incidents: {incidents}\n"
        f"- Integrations: {up}/{total} up\n"
        f"- Request: {user_message}\n\n"
        "MCP Status:\n" + "\n".join(mcp_lines) + "\n\n"
        "Model Output:\n"
        f"- {model_insight}\n\n"
        "Next Action:\n"
        f"1. {action_1}\n"
        "2. Confirm ServiceNow ticket update and Slack notification.\n"
        "3. Validate edge workload recovery and Kafka telemetry continuity."
    )
