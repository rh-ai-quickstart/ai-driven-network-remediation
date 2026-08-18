import json

_MAX_POD_LOG_LINES = 100

_JSON_SECTIONS = [
    ("pod_status", "Pod Status", None),
    ("cluster_events", "Cluster Events", 50),
    ("recent_errors", "Recent Errors", 30),
    ("log_search_results", "Log Search Results", 20),
]


def build_evidence_prompt(state) -> str:
    sections = []

    if state.resource_specs:
        sections.append("## Resource Configuration\n" + state.resource_specs)

    for attr, heading, limit in _JSON_SECTIONS:
        data = getattr(state, attr)
        if data:
            sections.append(f"## {heading}\n{json.dumps(data[:limit] if limit else data, indent=2)}")

    if state.pod_logs:
        lines = state.pod_logs.splitlines()[-_MAX_POD_LOG_LINES:]
        sections.append("## Pod Logs\n" + "\n".join(lines))

    return "\n\n".join(sections)
