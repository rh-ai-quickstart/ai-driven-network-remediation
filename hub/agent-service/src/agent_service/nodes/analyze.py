from loguru import logger

from agent_service.models import RootCauseAnalysis


def analyze_node(state: dict) -> dict:
    logger.info("Analyze node invoked")
    confidence = state.confidence_override if state.confidence_override is not None else 0.85
    rca = RootCauseAnalysis(
        root_cause="placeholder root cause",
        confidence=confidence,
        severity="medium",
        affected_components=["placeholder-component"],
        recommended_playbook="placeholder-playbook",
        reasoning="placeholder reasoning",
    )
    return {"root_cause_analysis": rca}
