import json
import time

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import ValidationError

from agent_service.config import ANALYZE_SYSTEM_PROMPT, get_llm
from agent_service.evidence import build_evidence_prompt
from agent_service.models import RootCauseAnalysis

_MAX_CONTEXT_CHARS = 5000


def _validate_diagnosis(diagnosis: object) -> tuple[RootCauseAnalysis, dict]:
    try:
        return RootCauseAnalysis.model_validate(diagnosis), {}
    except ValidationError as exc:
        if not (
            isinstance(diagnosis, dict)
            and diagnosis.get("selected_action") is not None
            and all(error["loc"] and error["loc"][0] == "selected_action" for error in exc.errors(include_input=False))
        ):
            raise

    rca = RootCauseAnalysis.model_validate({**diagnosis, "selected_action": None})
    logger.warning("Model returned an invalid remediation action; escalating with the diagnosis")
    return rca, {
        "decision": "escalate",
        "error_message": "The model supplied an invalid remediation action.",
    }


async def analyze_node(state: dict) -> dict:
    logger.info("Analyze node invoked")

    log_event = state.log_event
    context = "\n---\n".join(state.context_snippets or [])[:_MAX_CONTEXT_CHARS]

    user_content = f"Log event: {log_event.raw}\n\nRAG context:\n{context}"

    evidence = build_evidence_prompt(state)
    if evidence:
        user_content += f"\n\nInvestigation evidence:\n{evidence}"

    messages = [
        SystemMessage(content=ANALYZE_SYSTEM_PROMPT),
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

        rca, updates = _validate_diagnosis(json.loads(response.content))
        logger.debug("Root cause analysis: {}", rca.model_dump_json())

        usage = response.usage_metadata or {}
        tokens = usage.get("total_tokens", 0)

        return {
            **updates,
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
            summary="Analysis failed, escalate for manual review",
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
