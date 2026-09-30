"""Analyze node — LLM-powered root cause analysis for ML-detected RAN anomalies."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from ran_rca_service.config import get_llm
from ran_rca_service.models import RCAState

_SYSTEM_PROMPT = """\
You are a senior telco radio engineer specialising in RAN (Radio Access Network) root cause analysis.
You have deep expertise in 5G KPIs including RSRP, BLER, MCS, SNR, PRB utilization, throughput, and protocol behavior.

An ML-based anomaly detection model (Mantis AD on TelecomTS) has flagged a KPI window as anomalous.
Analyze the anomaly context and any vendor documentation, then produce a structured JSON diagnosis.

When recommending fixes, reference specific vendor documentation sections from the provided context where available.

Classify the root cause as exactly one category:
- antenna_misalignment: antenna tilt, azimuth, or alignment is degrading coverage.
- interference: noise, SINR degradation, or RF interference is the primary cause.
- scheduler_degradation: radio scheduling or resource-allocation behavior is degrading service.
- congestion: excess active-user demand is causing a transient load imbalance.
- capacity_exhaustion: sustained PRB or resource capacity is exhausted and must be expanded.
- cell_failure: a cell, radio, or supporting component has failed or is unavailable.
- unknown: the evidence does not support one of the other categories.

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

scheduler_degradation:
  Critical: RSRP mean=-85, SNR mean=20, BLER mean=0.05
  Variable: Poor MCS despite good SNR, inefficient PRB allocation
  Key: Good signal but inefficient resource use = scheduler problem

capacity_exhaustion:
  Critical: RSRP mean=-85, SNR mean=20
  Variable: PRB_Utilization sustained 95-100%, consistently maxed out
  Key: Good signal but resources constantly exhausted

MATCHING RULE: Compare the "Critical absolute indicators" and "Top 8 most variable KPIs" to these signatures.

Respond ONLY with valid JSON matching the provided schema:
{
  "root_cause_category": "<one of the defined categories>",
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
        "root_cause_category": {
            "type": "string",
            "enum": [
                "antenna_misalignment",
                "interference",
                "scheduler_degradation",
                "congestion",
                "capacity_exhaustion",
                "cell_failure",
                "unknown",
            ],
        },
        "root_cause": {"type": "string"},
        "recommended_fix": {"type": "string"},
    },
    "required": ["root_cause_category", "root_cause", "recommended_fix"],
}


async def analyze_node(state: RCAState) -> dict:
    context = "\n---\n".join(state.context_snippets or [])[:_MAX_CONTEXT_CHARS]

    kpi_summary = state.kpi_summary(top_n=8)

    # Also extract critical absolute values that may not be highly variable
    critical_kpis = _extract_critical_kpis(state.kpi_window)

    user_content = (
        f"Incident: {state.incident_id}\n"
        f"Zone: {state.zone}, Application: {state.application}\n"
        f"AD Label: {state.ad_label}, Confidence: {state.ad_confidence:.3f}\n"
        f"KPI Window: {len(state.kpi_window)} timesteps x 18 channels (TelecomTS 5G lab trace)"
    )
    if kpi_summary:
        user_content += f"\n\nTop 8 most variable KPIs:\n{kpi_summary}"
    if critical_kpis:
        user_content += f"\n\n{critical_kpis}"
    if context:
        user_content += f"\n\nVendor documentation context:\n{context}"

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    try:
        logger.debug(f"RCA LLM prompt for {state.incident_id}:\n{user_content[:800]}")
        if critical_kpis:
            logger.info(f"Critical indicators for {state.incident_id}: {critical_kpis.replace(chr(10), ' ')}")
        response = await get_llm().ainvoke(
            messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "RCAAnalysis", "schema": _RESPONSE_SCHEMA},
            },
        )
        if not isinstance(response.content, str):
            raise TypeError("LLM response content must be a JSON string")
        parsed = json.loads(response.content)
        logger.info(
            f"RCA category selected for {state.incident_id}: {parsed.get('root_cause_category', 'unknown')} "
            f"(zone={state.zone}, app={state.application})"
        )
        return {
            "root_cause_category": parsed.get("root_cause_category", "unknown"),
            "root_cause": parsed.get("root_cause", ""),
            "recommended_fix": parsed.get("recommended_fix", ""),
        }
    except Exception:  # noqa: BLE001 - graceful degradation must handle provider and parsing errors.
        logger.exception("LLM analysis failed — anomaly will flow through unenriched")
        return {"root_cause_category": "unknown", "root_cause": "", "recommended_fix": ""}
