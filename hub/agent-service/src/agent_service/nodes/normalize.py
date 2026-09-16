import json

from loguru import logger

from agent_service.edge_site import extract_edge_site_id_from_alert
from agent_service.models import LogEvent

_LEVEL_ALIASES = {
    "warning": "warn",
    "critical": "error",
}


def _normalize_level(raw_level: str) -> str:
    lowered = raw_level.lower()
    return _LEVEL_ALIASES.get(lowered, lowered)


def normalize_node(state: dict) -> dict:
    logger.info("Normalize node invoked")
    raw_event = state.raw_event
    kafka_offset = state.kafka_offset

    try:
        data = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, dict) and "kubernetes" in data:
        k8s = data.get("kubernetes", {})
        labels = data.get("labels", {})
        site_id = extract_edge_site_id_from_alert(data) or labels.get("edge_site_id", "unknown")
        if not site_id:
            site_id = "unknown"
        log_event = LogEvent(
            timestamp=data.get("@timestamp", "unknown"),
            message=data.get("message", "unknown"),
            level=_normalize_level(data.get("level", "unknown")),
            namespace=k8s.get("namespace_name", "unknown"),
            pod_name=k8s.get("pod_name", "unknown"),
            container=k8s.get("container_name", "unknown"),
            edge_site_id=site_id,
            kafka_offset=kafka_offset,
            raw=raw_event,
        )
    else:
        logger.warning("Could not parse raw_event as canonical JSON, using fallback defaults")
        log_event = LogEvent(
            timestamp="unknown",
            message=raw_event,
            level="unknown",
            namespace="unknown",
            pod_name="unknown",
            container="unknown",
            edge_site_id="unknown",
            kafka_offset=kafka_offset,
            raw=raw_event,
        )

    result = {"log_event": log_event}
    if isinstance(data, dict) and (incident_id := data.get("incident_id")):
        result["incident_id"] = str(incident_id)
    return result
