import json

_MAX_EVENTS = 50
_MAX_ERRORS = 30
_MAX_POD_LOG_LINES = 100
_MAX_LOG_SEARCH_RESULTS = 20


def build_evidence_prompt(state) -> str:
    sections = []

    if state.pod_status:
        sections.append(f"## Pod Status\n{json.dumps(state.pod_status, indent=2)}")

    if state.cluster_events:
        truncated = state.cluster_events[:_MAX_EVENTS]
        sections.append(f"## Cluster Events\n{json.dumps(truncated, indent=2)}")

    if state.recent_errors:
        truncated = state.recent_errors[:_MAX_ERRORS]
        sections.append(f"## Recent Errors\n{json.dumps(truncated, indent=2)}")

    if state.pod_logs:
        lines = state.pod_logs.splitlines()
        truncated = lines[-_MAX_POD_LOG_LINES:]
        sections.append(f"## Pod Logs\n" + "\n".join(truncated))

    if state.log_search_results:
        truncated = state.log_search_results[:_MAX_LOG_SEARCH_RESULTS]
        sections.append(f"## Log Search Results\n{json.dumps(truncated, indent=2)}")

    return "\n\n".join(sections)
