from loguru import logger

from agent_service.models import GraphConfig


def make_decide_node(config: GraphConfig):
    def decide_node(state: dict) -> dict:
        logger.info("Decide node invoked")
        confidence = state.root_cause_analysis.confidence
        if confidence > config.remediate_threshold:
            return {"decision": "execute"}
        if confidence < config.escalate_threshold:
            return {"decision": "escalate"}
        return {"decision": "request_approval"}

    return decide_node
