"""Helpers for coordinating with the spoke edge fast-path healer."""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from agent_service.config import FAST_PATH_COOLDOWN_SECONDS, FAST_PATH_LAST_HEAL_ANNOTATION
from agent_service.utils import derive_deployment_name, invoke_tool

_FAST_PATH_FAILURES = frozenset({"OOMKilled"})


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
    """Return True when the spoke fast-path healer acted within the cooldown window.

    Returns False (continue to AAP) when MCP is unreachable or the annotation is
    missing/stale. Must not raise.
    """
    cooldown = cooldown_seconds if cooldown_seconds is not None else FAST_PATH_COOLDOWN_SECONDS
    try:
        result = await invoke_tool(
            "get_deployment",
            {
                "deployment": deployment,
                "namespace": namespace,
                "edge_site_id": edge_site_id,
            },
        )
    except Exception:
        logger.opt(exception=True).warning(
            "spoke_fast_path_recent: get_deployment call failed; continuing to AAP"
        )
        return False
    if result.get("error") or not result.get("success", True):
        logger.warning(
            "spoke_fast_path_recent: get_deployment error={error}; continuing to AAP",
            error=result.get("error"),
        )
        return False
    annotations = result.get("annotations") or {}
    return fast_path_cooldown_active(annotations.get(FAST_PATH_LAST_HEAL_ANNOTATION), cooldown)


def target_deployment_name(pod_name: str) -> str | None:
    """Parent Deployment name from the Kafka pod name, or None.

    Uses the same ReplicaSet suffix strip as AAP extra_vars. Returns None when
    the name cannot be derived so remediate can continue to AAP.
    """
    derived = derive_deployment_name(pod_name or "")
    if not derived or derived == pod_name:
        return None
    return derived


def should_check_fast_path(failure_type: str | None) -> bool:
    return failure_type in _FAST_PATH_FAILURES
