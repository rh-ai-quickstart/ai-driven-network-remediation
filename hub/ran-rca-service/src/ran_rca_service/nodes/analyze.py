"""Analyze node — LLM-powered root cause analysis for RAN anomalies."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from ran_rca_service.config import get_llm
from ran_rca_service.models import RCAState

_SYSTEM_PROMPT = """\
You are a senior telco radio engineer specialising in RAN (Radio Access Network) root cause analysis.
You have deep expertise in O-RAN KPIs including RSRP, RSRQ, SINR, throughput, handover success rate, and cell availability.

Analyze the provided anomaly description and any vendor documentation context, then produce a structured JSON diagnosis.

When recommending fixes, reference specific vendor documentation sections (page numbers, section titles, table references) from the provided context where available.

Respond ONLY with valid JSON matching the provided schema:
{
  "root_cause": "<concise root cause explanation referencing RAN KPIs>",
  "recommended_fix": "<specific remediation steps referencing vendor doc sections>"
}"""

_MAX_CONTEXT_CHARS = 5000

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

    user_content = f"Anomaly: {state.anomaly}\nCell: {state.cell_id}, Band: {state.band}, Type: {state.anomaly_type}"
    if context:
        user_content += f"\n\nVendor documentation context:\n{context}"

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    try:
        response = await get_llm().ainvoke(
            messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "RCAAnalysis", "schema": _RESPONSE_SCHEMA},
            },
        )
        parsed = json.loads(response.content)
        return {
            "root_cause": parsed.get("root_cause", ""),
            "recommended_fix": parsed.get("recommended_fix", ""),
        }
    except Exception:
        logger.exception("LLM analysis failed — anomaly will flow through unenriched")
        return {"root_cause": "", "recommended_fix": ""}
