"""LLM chat: context building, model calls, and reply formatting."""

from __future__ import annotations

import logging

import httpx

from .config import MODEL_API_URL, MODEL_MAX_TOKENS, MODEL_NAME
from .models import EnrichedAnomaly, ModelSource

logger = logging.getLogger(__name__)

# Caps how many of the most recent buffered items go into the LLM prompt, so the
# context stays bounded even as the underlying deques fill up toward their much
# larger maxlen (ENRICHED_ANOMALIES_MAX_MESSAGES / REMEDIATION_RESULTS_MAX_MESSAGES).
_MAX_ITEMS_IN_PROMPT = 5


def _format_anomalies(anomalies: list[EnrichedAnomaly]) -> str:
    """Format enriched RAN anomalies for LLM context."""
    if not anomalies:
        return "No recent RAN anomalies detected."
    lines = []
    for a in anomalies[-_MAX_ITEMS_IN_PROMPT:]:
        lines.append(
            f"  - Incident {a.incident_id} (zone={a.zone}, app={a.application}) "
            f"[AD confidence: {a.ad_confidence:.2f}]\n"
            f"    Root cause: {a.root_cause or 'n/a'}\n"
            f"    Recommended fix: {a.recommended_fix or 'n/a'}"
        )
    return "\n".join(lines)


def _remediation_status(record: dict) -> str:
    """"successful"/"failed" reflect a completed remediation attempt: audit records
    are only published by ran-remediation-service's audit_node after remediate_node
    has already polled the AAP job to a terminal state (or given up), so `success`
    is always a definite bool in practice — never a "pending"/"in-progress" record.

    Still, RemediationConsumer doesn't schema-validate these dicts (unlike
    EnrichedAnomaly), so a malformed or older-schema message could be missing the
    `success` key entirely. Treat that as "pending" (unknown) rather than silently
    reporting a fix as "failed" when we don't actually know its outcome.
    """
    success = record.get("success")
    if success is None:
        return "pending"
    return "successful" if success else "failed"


def _format_remediations(remediations: list[dict]) -> str:
    """Format recent remediation results for LLM context."""
    if not remediations:
        return "No recent remediation results."
    lines = []
    for r in remediations[-_MAX_ITEMS_IN_PROMPT:]:
        status = _remediation_status(r)
        lines.append(
            f"  - Incident {r.get('incident_id')}: {status} "
            f"(template={r.get('template_name') or 'n/a'}, job_status={r.get('job_status') or 'n/a'}, "
            f"timed_out={r.get('timed_out', False)})\n"
            f"    Output: {r.get('output_summary') or 'n/a'}"
        )
    return "\n".join(lines)


def build_chat_context(
    user_message: str,
    anomalies: list[EnrichedAnomaly],
    remediations: list[dict],
    history: list[dict[str, str]],
) -> str:
    """Build a context-rich prompt for the LLM."""
    recent = history[-4:]
    convo = "\n".join(f"{item['role']}: {item['content']}" for item in recent) or "none"
    anomalies_context = _format_anomalies(anomalies)
    remediations_context = _format_remediations(remediations)

    return (
        "You are a telco RAN engineer assistant for an O-RAN anomaly detection and root cause "
        "analysis system using ML-based binary anomaly detection on TelecomTS 5G lab traces.\n"
        "Answer the operator's request directly with concise, actionable analysis about the "
        "detected anomalies below.\n"
        "When discussing an anomaly, mention: the incident ID, zone, application context, "
        "the AD confidence score, the likely root cause, and the recommended fix (including "
        "which vendor documentation section it references).\n"
        "When discussing remediation status, mention whether the fix succeeded, failed, or is "
        "pending (outcome not yet known), the job status, and whether it timed out.\n"
        "Do NOT mention cell IDs, bands, or rule-based anomaly types — this system uses "
        "ML-based detection on full KPI windows.\n"
        "Keep output under 250 words.\n\n"
        f"Model: {MODEL_NAME}\n\n"
        f"Recently detected RAN anomalies:\n{anomalies_context}\n\n"
        f"Recent remediation results:\n{remediations_context}\n\n"
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
        "messages": [{"role": "user", "content": prompt}],
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
