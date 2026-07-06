import json
import os
import time
from typing import get_args

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from loguru import logger

from agent_service.models import FailureType, RootCauseAnalysis

_LLAMASTACK_HOST = os.environ.get("LLAMASTACK_HOST", "localhost")
_LLAMASTACK_PORT = os.environ.get("LLAMASTACK_PORT", "8321")
_GRANITE_MODEL = os.environ.get("GRANITE_MODEL_NAME", "granite-4.0-8b")

_llm = ChatOpenAI(
    base_url=f"http://{_LLAMASTACK_HOST}:{_LLAMASTACK_PORT}/v1",
    model=_GRANITE_MODEL,
    api_key="unused",
)

_FAILURE_TYPES = ", ".join(get_args(FailureType))

_SYSTEM_PROMPT = f"""\
You are a senior NOC engineer performing root cause analysis on Kubernetes log events.
Analyze the provided log event and any retrieved runbook context, then produce a structured JSON diagnosis.

Valid failure_type values: {_FAILURE_TYPES}
Valid estimated_severity values: critical, high, medium, low

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

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    schema = RootCauseAnalysis.model_json_schema()

    try:
        t0 = time.monotonic()
        response = await _llm.ainvoke(
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
