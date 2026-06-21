from loguru import logger

from agent_service.models import GraphConfig

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
    def decide_node(state: dict) -> dict:
        logger.info("Decide node invoked")
        rca = state.root_cause_analysis
        confidence = rca.confidence
        if confidence < config.escalate_threshold:
            return {"decision": "escalate"}
        if confidence >= config.remediate_threshold:
            if rca.failure_type in KNOWN_PLAYBOOK_TYPES:
                return {"decision": "remediate"}
            return {"decision": "lightspeed"}
        return {"decision": "escalate"}

    return decide_node
