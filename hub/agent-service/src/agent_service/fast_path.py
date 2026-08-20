"""Helpers for coordinating with the spoke edge fast-path healer."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_service.config import FAST_PATH_COOLDOWN_SECONDS, FAST_PATH_DEPLOYMENT, FAST_PATH_LAST_HEAL_ANNOTATION
from agent_service.utils import invoke_tool

_RESTART_CLASS_FAILURES = frozenset({"OOMKilled", "CrashLoopBackOff"})


def fast_path_cooldown_active(annotation_value: str | None, cooldown_seconds: int) -> bool:
    if not annotation_value:
        return False
    try:
        last = datetime.fromisoformat(annotation_value.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
    return age.total_seconds() < cooldown_seconds


async def spoke_fast_path_recent(
    *,
    namespace: str,
    deployment: str,
    edge_site_id: str,
    cooldown_seconds: int | None = None,
) -> bool:
    """Return True when the spoke fast-path healer acted within the cooldown window."""
    cooldown = cooldown_seconds if cooldown_seconds is not None else FAST_PATH_COOLDOWN_SECONDS
    result = await invoke_tool(
        "get_deployment",
        {
            "deployment": deployment,
            "namespace": namespace,
            "edge_site_id": edge_site_id,
        },
    )
    if result.get("error") or not result.get("success", True):
        return False
    annotations = result.get("annotations") or {}
    return fast_path_cooldown_active(annotations.get(FAST_PATH_LAST_HEAL_ANNOTATION), cooldown)


def target_deployment_name(pod_name: str) -> str:
    if pod_name.startswith(f"{FAST_PATH_DEPLOYMENT}-"):
        return FAST_PATH_DEPLOYMENT
    return FAST_PATH_DEPLOYMENT


def should_check_fast_path(failure_type: str | None) -> bool:
    return failure_type in _RESTART_CLASS_FAILURES
