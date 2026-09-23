from loguru import logger

from agent_service.models import GraphConfig
from agent_service.nodes.match_guard import MatchVerdict, verify_playbook_match
from agent_service.nodes.remediate import _resolve_template

KNOWN_PLAYBOOK_TYPES = frozenset(
    {
        "OOMKilled",
        "CrashLoopBackOff",
        "ConfigError",
        "NetworkTimeout",
        "StorageFull",
    }
)


def make_decide_node(config: GraphConfig):
    async def decide_node(state) -> dict:
        logger.info("Decide node invoked")
        rca = state.root_cause_analysis
        confidence = rca.confidence

        if confidence < config.escalate_threshold:
            return {"decision": "escalate"}
        if confidence < config.remediate_threshold:
            return {"decision": "escalate"}
        if rca.failure_type not in KNOWN_PLAYBOOK_TYPES:
            return {"decision": "lightspeed"}

        # Overrides force a synthetic failure_type/confidence for testing while
        # summary/recommended_actions stay real; guarding that incoherent RCA
        # with the LLM is meaningless, so skip it and trust the resolved template.
        has_overrides = (
            state.confidence_override is not None and state.failure_type_override is not None
        )

        action = rca.recommended_actions[0] if rca.recommended_actions else None
        template = _resolve_template(action, rca.failure_type) if action else None
        if template:
            verdict = (
                await verify_playbook_match(rca, template)
                if config.enable_match_guard and not has_overrides
                else MatchVerdict.MATCH
            )
            if verdict == MatchVerdict.MATCH:
                return {"decision": "remediate", "selected_template": template}
            if verdict == MatchVerdict.ERROR:
                logger.warning("Playbook guard errored; escalating (fail-closed)")
                return {"decision": "escalate"}

        # No candidate template or guard returned NO_MATCH: the agent knows the
        # root cause but no prebuilt playbook fits. Generate a bespoke playbook
        # via lightspeed when confident enough, otherwise escalate to a human.
        if confidence >= config.lightspeed_threshold:
            return {"decision": "lightspeed"}
        return {"decision": "escalate"}

    return decide_node
