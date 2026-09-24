import json
from enum import Enum

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from agent_service.config import DECIDE_MATCH_PROMPT, get_llm

# One-line description of what each pre-built playbook does, for the guard prompt.
_TEMPLATE_DESCRIPTIONS: dict[str, str] = {
    "restart-nginx": "Restarts the nginx workload to recover a crashed or wedged pod.",
    "scale-up-workers": "Raises the memory limit / replica count to relieve resource exhaustion.",
    "clear-disk-space": "Frees disk on the node to recover from a full volume.",
}

_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": ["MATCH", "NO_MATCH"]},
    },
    "required": ["reasoning", "verdict"],
}


class MatchVerdict(str, Enum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    ERROR = "ERROR"


async def verify_playbook_match(rca, template: str) -> MatchVerdict:
    """Ask the LLM whether the candidate playbook actually fixes this root cause.

    Returns ERROR on any LLM failure so callers can fail closed (escalate).
    """
    description = _TEMPLATE_DESCRIPTIONS.get(template, "No description available.")
    human = (
        f"Root cause analysis:\n"
        f"  failure_type: {rca.failure_type}\n"
        f"  summary: {rca.summary}\n"
        f"  recommended_actions: {rca.recommended_actions}\n\n"
        f"Candidate playbook: {template}\n"
        f"What it does: {description}\n\n"
        f"Does running '{template}' resolve this root cause?"
    )
    messages = [
        SystemMessage(content=DECIDE_MATCH_PROMPT),
        HumanMessage(content=human),
    ]

    try:
        response = await get_llm().ainvoke(
            messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "PlaybookMatch", "schema": _MATCH_SCHEMA},
            },
        )
        parsed = json.loads(response.content)
        verdict = MatchVerdict(parsed["verdict"])
        logger.info(f"Playbook guard verdict for {template}: {verdict.value}")
        return verdict
    except Exception:
        logger.exception(f"Playbook guard failed for template {template}")
        return MatchVerdict.ERROR
