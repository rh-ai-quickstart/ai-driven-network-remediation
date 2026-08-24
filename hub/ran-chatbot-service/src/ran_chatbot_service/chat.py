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
    # anomalies is in ascending Kafka offset order (oldest first, see kafka.py's
    # seek-to-start_offset + forward iteration), so the 5 most recent are the tail.
    for a in anomalies[-5:]:
        ml_line = ""
        if a.ml_steer_used and a.ml_root_cause_class:
            ml_line = f"\n    ML class: {a.ml_root_cause_class} (confidence: {a.ml_confidence:.0%})"
        lines.append(
            f"  - Cell {a.cell_id} ({a.band}) [{a.anomaly_type}]: {a.anomaly}\n"
            f"    Root cause: {a.root_cause or 'n/a'}\n"
            f"    Recommended fix: {a.recommended_fix or 'n/a'}"
            f"{ml_line}"
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
        "analysis system.\n"
        "Answer the operator's request directly with concise, actionable analysis about the "
        "detected RAN cell anomalies below.\n"
        "When discussing an anomaly, mention: the affected cell/band, the anomaly type, the "
        "likely root cause, and the recommended fix (including which vendor documentation "
        "section it references).\n"
        "Do NOT repeat headers or formatting — just provide your insight.\n"
        "Keep output under 250 words.\n\n"
        f"Model: {MODEL_NAME}\n\n"
        f"Recently detected RAN anomalies:\n{anomalies_context}\n\n"
        f"Recent conversation: {convo}\n\n"
        f"Operator request: {user_message}\n\n"
        "Your analysis:"
    )


async def call_model(prompt: str, client: httpx.AsyncClient) -> tuple[str, str]:
    """Call the LLM endpoint using a shared, reused httpx client. Returns (reply_text, source).

    `client` is created once at app startup (see the `lifespan` in __init__.py) and
    passed in rather than constructed per call: httpx.AsyncClient is explicitly
    designed to be shared across concurrent requests within one event loop (unlike
    kafka-python's KafkaConsumer), so reusing it gives connection pooling/keep-alive
    to MODEL_API_URL instead of paying a fresh TCP/TLS handshake on every request.

    `source` is one of the fixed ModelSource values, or a dynamic
    ModelSource.http_error(code) string (e.g. "http-404") for HTTP errors —
    see models.py. Typed as plain `str` here (rather than ModelSource) since
    it's a mix of both; callers should still compare against ModelSource
    members (e.g. `source == ModelSource.LIVE`) rather than string literals.

    NOTE: Minimal implementation sufficient for V1 (single vLLM endpoint).
    Consider replacing with litellm/llama-index if we need streaming,
    multi-model fallback, or token management.
    """
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
                logger.debug("LLM replied with %d chars", len(text))
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
        cells_line = "- No RAN anomalies currently detected."
        root_cause = "n/a"
        recommended_fix = "n/a"
        ml_line = ""
    else:
        latest = anomalies[-1]
        cells_line = (
            f"- Latest anomaly: Cell {latest.cell_id} ({latest.band}) " f"[{latest.anomaly_type}] — {latest.anomaly}"
        )
        root_cause = latest.root_cause or "n/a"
        recommended_fix = latest.recommended_fix or "n/a"
        if latest.ml_steer_used and latest.ml_root_cause_class:
            ml_line = f"\n- ML class: {latest.ml_root_cause_class} (confidence: {latest.ml_confidence:.0%})"
        else:
            ml_line = ""

    if raw_reply:
        model_insight = raw_reply.strip()
    else:
        model_insight = "Live model unavailable; using deterministic operational fallback."

    return (
        "Summary:\n"
        f"- Anomalies detected: {len(anomalies)}\n"
        f"{cells_line}"
        f"{ml_line}\n"
        f"- Request: {user_message}\n\n"
        "Root Cause:\n"
        f"- {root_cause}\n\n"
        "Recommended Fix:\n"
        f"- {recommended_fix}\n\n"
        "Model Output:\n"
        f"- {model_insight}"
    )
