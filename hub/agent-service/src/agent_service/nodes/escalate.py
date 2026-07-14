from loguru import logger

from agent_service.utils import invoke_tool as _invoke_tool

_PRIORITY_MAP = {"critical": 1, "high": 2, "medium": 3, "low": 4}


def _build_description(log_event, rca, failed_attempts) -> str:
    lines = [
        f"Failure Type: {rca.failure_type}",
        f"Confidence: {rca.confidence}",
        f"Severity: {rca.estimated_severity}",
        f"Edge Site: {log_event.edge_site_id}",
        f"Namespace: {log_event.namespace}",
        f"Pod: {log_event.pod_name}",
        f"Container: {log_event.container}",
        "",
        "--- Root Cause Analysis ---",
        f"Summary: {rca.summary}",
        "Evidence:",
        *(f"  - {item}" for item in rca.evidence),
        "Recommended Actions:",
        *(f"  - {action}" for action in rca.recommended_actions),
        "",
        "--- Original Log Message ---",
        log_event.message,
    ]

    if failed_attempts:
        lines.append("")
        lines.append("--- Failed Remediation Attempts ---")
        for i, attempt in enumerate(failed_attempts, 1):
            parts = [f"Template: {attempt['template']}"]
            if "job_id" in attempt:
                parts.append(f"Job: {attempt['job_id']}")
            parts.append(f"Error: {attempt['error']}")
            lines.append(f"{i}. {' | '.join(parts)}")

    return "\n".join(lines) + "\n"


async def escalate_node(state) -> dict:
    log_event = state.log_event
    rca = state.root_cause_analysis

    short_description = (
        f"[AI-NOC] {rca.failure_type} – {log_event.pod_name}" f" in {log_event.namespace} ({log_event.edge_site_id})"
    )

    description = _build_description(log_event, rca, state.failed_attempts)
    priority = _PRIORITY_MAP.get(rca.estimated_severity, 4)

    logger.info(f"Creating ServiceNow incident: {short_description}")
    try:
        response = await _invoke_tool(
            "create_incident",
            {
                "short_description": short_description,
                "description": description,
                "priority": priority,
            },
        )
    except Exception as exc:
        reason = str(exc)
        logger.warning(f"ServiceNow escalation failed: {reason}")
        return {"servicenow_ticket": "", "error_message": reason}

    if not response.get("success"):
        reason = response.get("error", "unknown error")
        logger.warning(f"ServiceNow escalation failed: {reason}")
        return {"servicenow_ticket": "", "error_message": reason}

    ticket = response.get("number", "")
    logger.info(f"ServiceNow ticket created: {ticket}")
    return {"servicenow_ticket": ticket}
