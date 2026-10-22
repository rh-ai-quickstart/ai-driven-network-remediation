"""Analyze node — LLM-powered root cause analysis for ML-detected RAN anomalies."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from ran_rca_service.config import get_llm
from ran_rca_service.models import RCAState
from ran_rca_service.nodes.classify import RCA_CLASS_CATEGORIES

_SYSTEM_PROMPT = """\
You are a senior telco radio engineer specialising in RAN (Radio Access Network) root cause analysis.
You have deep expertise in 5G KPIs including RSRP, BLER, MCS, SNR, PRB utilization, throughput, and protocol behavior.

An ML-based anomaly detection model (Mantis AD on TelecomTS) has flagged a KPI window as anomalous.
Analyze the anomaly context and any vendor documentation, then produce a structured JSON diagnosis.

When recommending fixes, reference specific vendor documentation sections from the provided context where available.

Mantis RCA supplies the operational category used for remediation. Explain the
predicted fault and recommend a fix using the KPI and vendor evidence. Do not
select a different category or remediation job.

RSRP INTERPRETATION (5G signal strength):
  > -90 dBm = Excellent signal
  -90 to -100 dBm = Good signal
  -100 to -110 dBm = POOR signal (coverage/antenna issue)
  < -110 dBm = Very poor signal

CATEGORY SIGNATURES (match the KPI pattern):

antenna_misalignment:
  Critical: RSRP mean=-102, SNR mean=15, BLER mean=0.11
  Variable: TX_Bytes (200K-450K range), DL_NumberOfPackets swings
  Key: Poor signal BUT low error rate means antenna/coverage issue, NOT interference

interference:
  Critical: RSRP mean=-106, SNR mean=14, BLER mean=0.14-0.49
  Variable: UL_SNR highly variable (5-21 dB), elevated BLER throughout
  Key: Poor signal PLUS high error rate = RF interference/noise

congestion:
  Critical: RSRP mean=-88, SNR mean=21, BLER mean=0.11
  Variable: Estimated_UL_Buffer (40 → 47,000!), TX_Bytes spikes, PRB bursts
  Key: GOOD signal but buffer exhaustion = too much traffic

resource_allocation_fault:
  Variable: Poor MCS despite adequate SNR, inefficient PRB allocation
  Key: Resource Allocation Bugs indicates an allocation fault; explain the KPI evidence and propose a correction plan

capacity_exhaustion:
  Critical: RSRP mean=-85, SNR mean=20
  Variable: PRB_Utilization sustained 95-100%, consistently maxed out
  Key: Good signal but resources constantly exhausted

MATCHING RULE: Compare the "Critical absolute indicators" and "Top 8 most variable KPIs" to these signatures.

Respond ONLY with valid JSON matching the provided schema:
{
  "root_cause": "<concise root cause explanation referencing 5G KPIs>",
  "recommended_fix": "<specific remediation steps referencing vendor doc sections>"
}"""

_MAX_CONTEXT_CHARS = 5000


def _extract_critical_kpis(kpi_window: list[dict]) -> str:
    """Extract absolute levels of critical indicators (RSRP, SNR, BLER) that may not be highly variable."""
    if not kpi_window:
        return ""

    critical_channels = ["RSRP", "UL_SNR", "UL_BLER"]
    lines = []

    for ch in critical_channels:
        vals = [row[ch] for row in kpi_window if isinstance(row.get(ch), (int, float))]
        if vals:
            mn, mx = min(vals), max(vals)
            avg = sum(vals) / len(vals)
            lines.append(f"  {ch}: min={mn:.2f} mean={avg:.2f} max={mx:.2f}")

    if not lines:
        return ""

    return "Critical absolute indicators (RSRP, SNR, BLER):\n" + "\n".join(lines)


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "recommended_fix": {"type": "string"},
    },
    "required": ["root_cause", "recommended_fix"],
}


async def analyze_node(state: RCAState) -> dict:
    context = "\n---\n".join(state.context_snippets or [])[:_MAX_CONTEXT_CHARS]

    kpi_summary = state.kpi_summary(top_n=8)

    # Also extract critical absolute values that may not be highly variable
    critical_kpis = _extract_critical_kpis(state.kpi_window)

    user_content = (
        f"Incident: {state.incident_id}\n"
        f"Zone: {state.zone}, Application: {state.application}\n"
        f"AD Label: {state.ad_label}, AD Confidence: {state.ad_confidence:.3f}\n"
        f"KPI Window: {len(state.kpi_window)} timesteps x 18 channels (TelecomTS 5G lab trace)"
    )
    if kpi_summary:
        user_content += f"\n\nTop 8 most variable KPIs:\n{kpi_summary}"
    if critical_kpis:
        user_content += f"\n\n{critical_kpis}"
    if state.ml_class:
        user_content += (
            f"\n\nMantis RCA prediction: {state.ml_class} "
            f"(confidence {state.ml_confidence:.3f}); operational category: "
            f"{RCA_CLASS_CATEGORIES.get(state.ml_class, 'unknown')}. "
            "Explain this diagnosis and recommend a fix; do not choose a category or job."
        )
    if context:
        user_content += f"\n\nVendor documentation context:\n{context}"

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    logger.debug(f"RCA LLM prompt for {state.incident_id}:\n{user_content[:800]}")
    if critical_kpis:
        logger.info(f"Critical indicators for {state.incident_id}: {critical_kpis.replace(chr(10), ' ')}")
    category = RCA_CLASS_CATEGORIES.get(state.ml_class, "unknown")
    try:
        response = await get_llm().ainvoke(
            messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "RCAAnalysis", "schema": _RESPONSE_SCHEMA},
            },
        )
        if not isinstance(response.content, str):
            raise ValueError("invalid LLM response")
        parsed = json.loads(response.content)
        if not isinstance(parsed, dict) or not all(
            isinstance(parsed.get(field), str) and parsed[field].strip()
            for field in ("root_cause", "recommended_fix")
        ):
            raise ValueError("invalid LLM explanation")
    except Exception as exc:  # noqa: BLE001 - an explanation failure must not stop remediation.
        logger.warning("LLM explanation failed for incident_id={}: {}", state.incident_id, type(exc).__name__)
        if isinstance(exc, TimeoutError):
            reason = "LLM timed out"
        elif isinstance(exc, (json.JSONDecodeError, ValueError, TypeError)):
            reason = "invalid LLM response"
        else:
            reason = "LLM unavailable"
        return {"root_cause_category": category, "root_cause": "", "recommended_fix": "", "llm_error_reason": reason}

    logger.info("Mantis RCA category for {}: {} (zone={}, app={})", state.incident_id, category, state.zone, state.application)
    return {"root_cause_category": category, "root_cause": parsed["root_cause"], "recommended_fix": parsed["recommended_fix"], "llm_error_reason": ""}
