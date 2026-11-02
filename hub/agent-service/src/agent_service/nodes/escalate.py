from loguru import logger

from agent_service.models import RemediationResult
from agent_service.utils import invoke_tool as _invoke_tool

_PRIORITY_MAP = {"critical": 1, "high": 2, "medium": 3, "low": 4}
_DEFAULT_PRIORITY = 4
_AAP_TOOL = "aap"


def _failed_remediation_result_lines(remediation_result: RemediationResult | None) -> list[str]:
    if remediation_result is None or remediation_result.success:
        return []

    failure_lines = [
        "",
        "--- Failed Remediation Result ---",
        f"Tool: {remediation_result.tool_used}",
        f"Action: {remediation_result.action_taken}",
    ]
    if remediation_result.tool_used == _AAP_TOOL and remediation_result.job_id:
        failure_lines.append(f"Job ID: {remediation_result.job_id}")
    failure_lines.append(f"Output: {remediation_result.output_summary}")
    return failure_lines


def _build_description(log_event, rca, remediation_result=None) -> str:
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

    lines.extend(_failed_remediation_result_lines(remediation_result))

    return "\n".join(lines) + "\n"


async def escalate_node(state) -> dict:
    log_event = state.log_event
    rca = state.root_cause_analysis

    short_description = (
        f"[AI-NOC] {rca.failure_type} – {log_event.pod_name}" f" in {log_event.namespace} ({log_event.edge_site_id})"
    )

    description = _build_description(log_event, rca, state.remediation_result)
    priority = _PRIORITY_MAP.get(rca.estimated_severity, _DEFAULT_PRIORITY)

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

    ticket = response.get("ticket_number", "")
    incident_url = response.get("incident_url", "")
    logger.info(f"ServiceNow ticket created: {ticket} url={incident_url}")
    return {"servicenow_ticket": ticket}
