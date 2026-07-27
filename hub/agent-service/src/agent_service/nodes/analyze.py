import json
import time
from typing import get_args

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from agent_service.config import get_llm
from agent_service.evidence import build_evidence_prompt
from agent_service.models import FailureType, RootCauseAnalysis

_FAILURE_TYPES = ", ".join(get_args(FailureType))

_SYSTEM_PROMPT = f"""\
You are a senior NOC engineer performing root cause analysis on Kubernetes log events.
Analyze the provided log event, any retrieved runbook context and investigation evidence, then produce a structured JSON diagnosis.

Valid failure_type values: {_FAILURE_TYPES}
Valid estimated_severity values: critical, high, medium, low

IMPORTANT: recommended_actions must contain SHORT executable remediation names, not diagnostic commands.
Use action names like: "restart nginx service", "scale up workers", "clear disk space", "fix configuration".
Do NOT put shell commands (oc logs, kubectl describe, etc.) in recommended_actions — those are diagnostic, not remediation.

Respond ONLY with valid JSON matching the provided schema."""

_MAX_CONTEXT_CHARS = 5000


async def analyze_node(state: dict) -> dict:
    logger.info("Analyze node invoked")

    # For testing
    if state.confidence_override is not None and state.failure_type_override is not None:
        log_event = state.log_event
        rca = RootCauseAnalysis(
            failure_type=state.failure_type_override,
            confidence=state.confidence_override,
            summary=log_event.message if log_event else "synthetic override",
            evidence=[log_event.raw] if log_event else ["override"],
            recommended_actions=["manual review"],
            estimated_severity="medium",
            runbook_reference="n/a",
        )
        return {"root_cause_analysis": rca}

    log_event = state.log_event
    context = "\n---\n".join(state.context_snippets or [])[:_MAX_CONTEXT_CHARS]

    user_content = f"Log event: {log_event.raw}\n\nRAG context:\n{context}"

    evidence = build_evidence_prompt(state)
    if evidence:
        user_content += f"\n\nInvestigation evidence:\n{evidence}"

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    schema = RootCauseAnalysis.model_json_schema()

    try:
        t0 = time.monotonic()
        response = await get_llm().ainvoke(
            messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "RootCauseAnalysis", "schema": schema},
            },
        )
        latency_ms = (time.monotonic() - t0) * 1000

        rca = RootCauseAnalysis.model_validate(json.loads(response.content))

        usage = response.usage_metadata or {}
        tokens = usage.get("total_tokens", 0)

        return {
            "root_cause_analysis": rca,
            "analysis_tokens_used": tokens,
            "analysis_latency_ms": latency_ms,
        }
    except Exception:
        logger.exception("LLM analysis failed")
        latency_ms = (time.monotonic() - t0) * 1000
        fallback = RootCauseAnalysis(
            failure_type="Unknown",
            confidence=0.0,
            summary="Analysis failed — escalate for manual review",
            evidence=[],
            recommended_actions=["escalate to on-call engineer"],
            estimated_severity="critical",
            runbook_reference="n/a",
        )
        return {
            "root_cause_analysis": fallback,
            "analysis_tokens_used": 0,
            "analysis_latency_ms": latency_ms,
        }
