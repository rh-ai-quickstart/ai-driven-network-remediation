"""Helpers for coordinating with the spoke edge fast-path healer."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loguru import logger

from agent_service.config import FAST_PATH_COOLDOWN_SECONDS, FAST_PATH_LAST_HEAL_ANNOTATION
from agent_service.utils import invoke_tool, resolve_remediation_deployment

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


def is_demo_oom_kafka_alert(raw_event: str) -> bool:
    """True for dashboard Trigger OOM Demo payloads (synthetic Kafka alert)."""
    if not raw_event:
        return False
    try:
        data = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    labels = data.get("labels")
    if not isinstance(labels, dict):
        return False
    return str(labels.get("dark_noc_scenario", "")).lower() == "oom"


def spoke_fast_path_actuated(
    annotation_value: str | None,
    *,
    cooldown_seconds: int,
    demo_oom_alert: bool = False,
) -> bool:
    if not annotation_value:
        return False
    if fast_path_cooldown_active(annotation_value, cooldown_seconds):
        return True
    # UC3 demo: heal on spoke first, then synthetic OOM alert (may be after cooldown).
    if demo_oom_alert:
        return True
    return False


async def spoke_fast_path_recent(
    *,
    namespace: str,
    deployment: str,
    edge_site_id: str,
    cooldown_seconds: int | None = None,
    raw_event: str = "",
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
    return spoke_fast_path_actuated(
        annotations.get(FAST_PATH_LAST_HEAL_ANNOTATION),
        cooldown_seconds=cooldown,
        demo_oom_alert=is_demo_oom_kafka_alert(raw_event),
    )


def target_deployment_name(pod_name: str, namespace: str = "") -> str | None:
    """Parent Deployment name from the alert pod, or None.

    Uses the same rules as AAP extra_vars (including demo nginx-edge → edge-nginx).
    Returns None when the name cannot be derived so remediate can continue to AAP.
    """
    pod = pod_name or ""
    deployment = resolve_remediation_deployment(namespace or "", pod)
    if not deployment or deployment == pod:
        return None
    return deployment


def should_check_fast_path(failure_type: str | None, raw_event: str = "") -> bool:
    if failure_type in _FAST_PATH_FAILURES:
        return True
    return is_demo_oom_kafka_alert(raw_event)
