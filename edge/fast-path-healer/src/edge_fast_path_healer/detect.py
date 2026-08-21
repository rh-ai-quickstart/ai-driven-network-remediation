from __future__ import annotations

from datetime import datetime, timezone

from edge_fast_path_healer.remediate import parse_memory_mi

DEFAULT_OOM_MAX_AGE_SECONDS = 300


def _finished_at_is_recent(finished_at: str | None, max_age_seconds: int) -> bool:
    if not finished_at:
        return False
    try:
        last = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
    return age.total_seconds() < max_age_seconds


def pod_oom_event_key(
    pod: dict, *, max_age_seconds: int = DEFAULT_OOM_MAX_AGE_SECONDS
) -> str | None:
    name = pod.get("metadata", {}).get("name", "")
    statuses = pod.get("status", {}).get("containerStatuses", [])
    for status in statuses:
        term = (status.get("lastState") or {}).get("terminated") or (status.get("state") or {}).get("terminated")
        if not term or term.get("reason") != "OOMKilled":
            continue
        finished = term.get("finishedAt")
        if not _finished_at_is_recent(finished, max_age_seconds):
            continue
        return f"{name}:{status.get('name', 'container')}:{finished}"
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
