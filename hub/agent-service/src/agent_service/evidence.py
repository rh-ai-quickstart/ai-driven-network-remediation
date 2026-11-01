import json

_MAX_POD_LOG_LINES_ATTACHMENT = 30
_MAX_POD_LOG_CHARS = 4000

_PROMPT_SECTIONS = [
    ("recent_errors", "Recent Errors", 10),
    ("log_search_results", "Log Search Results", 10),
]

_ATTACHMENT_SECTIONS = [
    ("pod_status", "Pod Status", 5),
    ("cluster_events", "Cluster Events", 15),
]


def _limit_data(data, limit):
    return data[:limit] if limit and isinstance(data, list) else data


def build_evidence_prompt(state) -> str:
    sections = []

    if state.resource_specs:
        sections.append("## Resource Configuration\n" + state.resource_specs)

    pod_logs = get_pod_logs_for_attachment(state)
    if pod_logs:
        sections.append("## Pod Logs\n" + pod_logs)

    for attr, heading, limit in _PROMPT_SECTIONS:
        data = getattr(state, attr)
        if data:
            sections.append(f"## {heading}\n{json.dumps(_limit_data(data, limit), indent=2)}")

    return "\n\n".join(sections)


def get_pod_logs_for_attachment(state) -> str:
    """Return truncated pod logs suitable for an ALS attachment."""
    if not state.pod_logs:
        return ""
    lines = state.pod_logs.splitlines()[-_MAX_POD_LOG_LINES_ATTACHMENT:]
    while len(lines) > 1 and len("\n".join(lines)) > _MAX_POD_LOG_CHARS:
        lines.pop(0)
    return "\n".join(lines)[:_MAX_POD_LOG_CHARS]


def get_structured_attachments(state) -> list[dict]:
    """Return pod status and cluster events as ALS attachments."""
    attachments = []
    for attr, heading, limit in _ATTACHMENT_SECTIONS:
        data = getattr(state, attr, None)
        if data:
            content = json.dumps(_limit_data(data, limit), indent=2)
            attachments.append(
                {"attachment_type": "configuration", "content_type": "application/json", "content": content}
            )
    return attachments


def build_grounding_text(state) -> str:
    """Full evidence text for grounding checks (includes attachment sections)."""
    sections = []

    if state.resource_specs:
        sections.append(state.resource_specs)

    pod_logs = get_pod_logs_for_attachment(state)
    if pod_logs:
        sections.append(pod_logs)

    for attr, _, limit in _PROMPT_SECTIONS + _ATTACHMENT_SECTIONS:
        data = getattr(state, attr, None)
        if data:
            sections.append(json.dumps(_limit_data(data, limit)))

    return "\n".join(sections)
