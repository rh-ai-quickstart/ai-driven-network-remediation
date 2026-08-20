from __future__ import annotations

from edge_fast_path_healer.remediate import parse_memory_mi


def pod_oom_event_key(pod: dict) -> str | None:
    name = pod.get("metadata", {}).get("name", "")
    statuses = pod.get("status", {}).get("containerStatuses", [])
    for status in statuses:
        term = (status.get("lastState") or {}).get("terminated") or (status.get("state") or {}).get("terminated")
        if term and term.get("reason") == "OOMKilled":
            return f"{name}:{status.get('name', 'container')}:{term.get('finishedAt', 'unknown')}"
    return None


def deployment_memory_limit_mi(deployment: dict) -> int | None:
    containers = (
        deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    if not containers:
        return None
    mem = (containers[0].get("resources") or {}).get("limits", {}).get("memory", "")
    return parse_memory_mi(mem)


def unsafe_limit_event_key(limit_mi: int | None, threshold_mi: int) -> str | None:
    if limit_mi is not None and limit_mi <= threshold_mi:
        return f"unsafe-limit:{limit_mi}"
    return None
