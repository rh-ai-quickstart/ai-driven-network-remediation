"""LLM chat: context building, model calls, and reply formatting."""

from __future__ import annotations

import logging

import httpx

from .config import MODEL_API_URL, MODEL_MAX_TOKENS, MODEL_NAME
from .models import EnrichedAnomaly, ModelSource

logger = logging.getLogger(__name__)


def _format_anomalies(anomalies: list[EnrichedAnomaly]) -> str:
    """Format enriched RAN anomalies for LLM context."""
    if not anomalies:
        return "No recent RAN anomalies detected."
    lines = []
    for a in anomalies[-5:]:
        lines.append(
            f"  - Incident {a.incident_id} (zone={a.zone}, app={a.application}) "
            f"[AD confidence: {a.ad_confidence:.2f}]\n"
            f"    Root cause: {a.root_cause or 'n/a'}\n"
            f"    Recommended fix: {a.recommended_fix or 'n/a'}"
        )
    return "\n".join(lines)


def build_chat_context(
    user_message: str,
    anomalies: list[EnrichedAnomaly],
    history: list[dict[str, str]],
) -> str:
    """Build a context-rich prompt for the LLM."""
    recent = history[-4:]
    convo = "\n".join(f"{item['role']}: {item['content']}" for item in recent) or "none"
    anomalies_context = _format_anomalies(anomalies)

    return (
        "You are a telco RAN engineer assistant for an O-RAN anomaly detection and root cause "
        "analysis system using ML-based binary anomaly detection on TelecomTS 5G lab traces.\n"
        "Answer the operator's request directly with concise, actionable analysis about the "
        "detected anomalies below.\n"
        "When discussing an anomaly, mention: the incident ID, zone, application context, "
        "the AD confidence score, the likely root cause, and the recommended fix (including "
        "which vendor documentation section it references).\n"
        "Do NOT mention cell IDs, bands, or rule-based anomaly types — this system uses "
        "ML-based detection on full KPI windows.\n"
        "Keep output under 250 words.\n\n"
        f"Model: {MODEL_NAME}\n\n"
        f"Recently detected RAN anomalies:\n{anomalies_context}\n\n"
        f"Recent conversation: {convo}\n\n"
        f"Operator request: {user_message}\n\n"
        "Your analysis:"
    )


async def call_model(prompt: str, client: httpx.AsyncClient) -> tuple[str, str]:
    """Call the LLM endpoint. Returns (reply_text, source)."""
    if not MODEL_API_URL:
        return "", ModelSource.DISABLED
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "max_tokens": MODEL_MAX_TOKENS,
        "temperature": 0.2,
    }
    try:
        resp = await client.post(MODEL_API_URL, json=payload)
        if resp.status_code != 200:
            logger.warning("LLM returned HTTP %d from %s", resp.status_code, MODEL_API_URL)
            return "", ModelSource.http_error(resp.status_code)
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            text = (choices[0].get("text") or choices[0].get("message", {}).get("content") or "").strip()
            if text:
                return text, ModelSource.LIVE
        return "", ModelSource.EMPTY
    except Exception:
        logger.warning("LLM unreachable at %s", MODEL_API_URL, exc_info=True)
        return "", ModelSource.UNREACHABLE


def format_chat_reply(
    user_message: str,
    raw_reply: str,
    anomalies: list[EnrichedAnomaly],
) -> str:
    """Format LLM output into a structured reply, or generate a deterministic fallback."""
    if not anomalies:
        anomaly_line = "- No RAN anomalies currently detected."
        root_cause = "n/a"
        recommended_fix = "n/a"
    else:
        latest = anomalies[-1]
        anomaly_line = (
            f"- Latest anomaly: Incident {latest.incident_id} "
            f"(zone={latest.zone}, app={latest.application}) "
            f"[AD confidence: {latest.ad_confidence:.2f}]"
        )
        root_cause = latest.root_cause or "n/a"
        recommended_fix = latest.recommended_fix or "n/a"

    if raw_reply:
        model_insight = raw_reply.strip()
    else:
        model_insight = "Live model unavailable; using deterministic operational fallback."

    return (
        "Summary:\n"
        f"- Anomalies detected: {len(anomalies)}\n"
        f"{anomaly_line}\n"
        f"- Request: {user_message}\n\n"
        "Root Cause:\n"
        f"- {root_cause}\n\n"
        "Recommended Fix:\n"
        f"- {recommended_fix}\n\n"
        "Model Output:\n"
        f"- {model_insight}"
    )
