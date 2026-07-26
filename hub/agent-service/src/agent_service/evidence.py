import json

_MAX_EVENTS = 50
_MAX_ERRORS = 30


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

    return "\n\n".join(sections)
