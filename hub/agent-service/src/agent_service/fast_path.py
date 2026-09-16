"""Helpers for coordinating with the spoke edge fast-path healer.

Demo Kafka payloads (chatbot BFF) drive several branches here. They are not
Grafana or ClusterLogForwarder production alerts.

- ``labels.dark_noc_scenario`` or top-level ``incident_id``: dashboard demo trigger.
- ``oom``: UC3 spoke heal then synthetic OOM within ``DEMO_OOM_FAST_PATH_MAX_GAP_SECONDS``;
  skip duplicate hub AAP (including after cooldown). Older heals do not skip; hub launches AAP.
- ``crashloop`` / ``lightspeed``: always run hub AAP (never skip on spoke heal markers).
- Live CLF JSON (``kubernetes`` block, no demo labels): optional early suppress in
  ``server.py`` during remediation cooldown to avoid log feedback loops. Suppressed
  messages do not run the LangGraph workflow (no Slack notify or incident-audit).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loguru import logger

from agent_service.config import (
    DEMO_OOM_FAST_PATH_MAX_GAP_SECONDS,
    FAST_PATH_COOLDOWN_SECONDS,
    FAST_PATH_LAST_HEAL_ANNOTATION,
    ROLLOUT_RESTART_ANNOTATION,
)
from agent_service.edge_site import extract_edge_site_id_from_alert, resolve_edge_site_id
from agent_service.models import LogEvent
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


def _demo_scenario(raw_event: str) -> str:
    if not raw_event:
        return ""
    try:
        data = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    labels = data.get("labels")
    if not isinstance(labels, dict):
        return ""
    return str(labels.get("dark_noc_scenario", "")).lower()


def is_demo_oom_kafka_alert(raw_event: str) -> bool:
    """True for dashboard Trigger OOM Demo payloads (synthetic Kafka alert)."""
    return _demo_scenario(raw_event) == "oom"


def is_clf_shaped_kafka_event(raw_event: str) -> bool:
    """True when raw_event looks like ClusterLogForwarder output (not canonical BFF JSON)."""
    if not raw_event.strip():
        return False
    try:
        data = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    k8s = data.get("kubernetes")
    return isinstance(k8s, dict) and bool(k8s.get("namespace_name") or k8s.get("pod_name"))


def is_dashboard_demo_kafka_event(raw_event: str) -> bool:
    """True for BFF demo triggers (incident_id or dark_noc_scenario label)."""
    if not raw_event.strip():
        return False
    try:
        data = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("incident_id"):
        return True
    labels = data.get("labels")
    return isinstance(labels, dict) and bool(labels.get("dark_noc_scenario"))


def demo_requires_hub_aap(raw_event: str) -> bool:
    """Demo buttons that must run hub/AAP even when a spoke fast-path heal is recent."""
    return _demo_scenario(raw_event) in {"crashloop", "lightspeed"}


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _alert_timestamp_from_raw_event(raw_event: str) -> str | None:
    if not raw_event.strip():
        return None
    try:
        data = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    ts = data.get("@timestamp")
    return str(ts) if ts else None


def demo_oom_alert_follows_spoke_heal(
    fast_heal: str | None,
    raw_event: str,
    *,
    cooldown_seconds: int,
    max_gap_seconds: int | None = None,
) -> bool:
    """True when a dashboard OOM demo alert should skip hub AAP after a spoke heal (UC3)."""
    if not is_demo_oom_kafka_alert(raw_event) or not fast_heal:
        return False
    if fast_path_cooldown_active(fast_heal, cooldown_seconds):
        return True
    gap_limit = max_gap_seconds if max_gap_seconds is not None else DEMO_OOM_FAST_PATH_MAX_GAP_SECONDS
    heal_dt = _parse_iso_timestamp(fast_heal)
    alert_dt = _parse_iso_timestamp(_alert_timestamp_from_raw_event(raw_event))
    if heal_dt is None or alert_dt is None:
        return False
    gap = (alert_dt.astimezone(timezone.utc) - heal_dt.astimezone(timezone.utc)).total_seconds()
    return 0 <= gap <= gap_limit


def deployment_remediation_actuation_source(
    annotations: dict,
    *,
    cooldown_seconds: int,
    raw_event: str = "",
) -> str | None:
    """Return spoke/hub when a recent heal or rollout restart should skip duplicate AAP jobs."""
    fast_heal = annotations.get(FAST_PATH_LAST_HEAL_ANNOTATION)
    restart_at = annotations.get(ROLLOUT_RESTART_ANNOTATION)

    if fast_path_cooldown_active(fast_heal, cooldown_seconds):
        return "spoke"
    if fast_path_cooldown_active(restart_at, cooldown_seconds):
        return "hub"
    if demo_oom_alert_follows_spoke_heal(
        fast_heal,
        raw_event,
        cooldown_seconds=cooldown_seconds,
    ):
        return "spoke"
    return None


def deployment_remediation_recently_actuated(
    annotations: dict,
    *,
    cooldown_seconds: int,
    raw_event: str = "",
) -> bool:
    return (
        deployment_remediation_actuation_source(
            annotations,
            cooldown_seconds=cooldown_seconds,
            raw_event=raw_event,
        )
        is not None
    )


def spoke_fast_path_actuated(
    annotation_value: str | None,
    *,
    cooldown_seconds: int,
    demo_oom_alert: bool = False,
    raw_event: str = "",
) -> bool:
    """Backward-compatible wrapper for tests that only pass the fast-path heal annotation."""
    if demo_oom_alert and not raw_event:
        raw_event = '{"labels":{"dark_noc_scenario":"oom"}}'
    return deployment_remediation_recently_actuated(
        {FAST_PATH_LAST_HEAL_ANNOTATION: annotation_value or ""},
        cooldown_seconds=cooldown_seconds,
        raw_event=raw_event,
    )


async def recent_deployment_remediation_actuation(
    *,
    namespace: str,
    deployment: str,
    edge_site_id: str,
    cooldown_seconds: int | None = None,
    raw_event: str = "",
) -> str | None:
    """Return spoke/hub when duplicate AAP should be skipped, else None to launch a job.

    Returns None (continue to AAP) when MCP is unreachable or markers are missing/stale.
    Must not raise.
    """
    if demo_requires_hub_aap(raw_event):
        return None
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
            "recent_deployment_remediation_actuation: get_deployment failed; continuing to AAP"
        )
        return None
    if result.get("error") or not result.get("success", True):
        logger.warning(
            "recent_deployment_remediation_actuation: get_deployment error={error}; continuing to AAP",
            error=result.get("error"),
        )
        return None
    annotations = result.get("annotations") or {}
    return deployment_remediation_actuation_source(
        annotations,
        cooldown_seconds=cooldown,
        raw_event=raw_event,
    )


async def should_suppress_clf_feedback_loop(raw_event: str) -> bool:
    """Skip full graph for live CLF logs while a deployment is in remediation cooldown.

    Demo Kafka events (dashboard buttons) are never suppressed. When this returns
    True, the Kafka consumer does not invoke LangGraph (no incident-audit or notify).
    """
    if is_dashboard_demo_kafka_event(raw_event):
        return False
    try:
        data = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    k8s = data.get("kubernetes")
    if not isinstance(k8s, dict):
        return False
    namespace = str(k8s.get("namespace_name") or "").strip()
    pod_name = str(k8s.get("pod_name") or "").strip()
    if not namespace or not pod_name:
        return False
    site_from_labels = extract_edge_site_id_from_alert(data) or "unknown"
    log_event = LogEvent(
        timestamp=str(data.get("@timestamp", "unknown")),
        message=str(data.get("message", "")),
        level=str(data.get("level", "unknown")),
        namespace=namespace,
        pod_name=pod_name,
        container=str(k8s.get("container_name", "unknown")),
        edge_site_id=site_from_labels,
        kafka_offset=0,
        raw=raw_event,
    )
    edge_site_id = resolve_edge_site_id(log_event, raw_event=raw_event)
    deployment = target_deployment_name(pod_name, namespace)
    if not deployment:
        return False
    actuation = await recent_deployment_remediation_actuation(
        namespace=namespace,
        deployment=deployment,
        edge_site_id=edge_site_id,
        raw_event=raw_event,
    )
    return actuation is not None


async def spoke_fast_path_recent(
    *,
    namespace: str,
    deployment: str,
    edge_site_id: str,
    cooldown_seconds: int | None = None,
    raw_event: str = "",
) -> bool:
    """Return True when a recent heal or rollout restart should skip duplicate AAP jobs."""
    return (
        await recent_deployment_remediation_actuation(
            namespace=namespace,
            deployment=deployment,
            edge_site_id=edge_site_id,
            cooldown_seconds=cooldown_seconds,
            raw_event=raw_event,
        )
        is not None
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


def should_consult_spoke_fast_path(failure_type: str | None, raw_event: str = "") -> bool:
    """Whether remediate should read deployment heal markers before launch_job.

    Deprecated for routing: remediate always checks when a target deployment is known.
    Kept for unit tests and callers that gate optional fast-path reads.
    """
    if demo_requires_hub_aap(raw_event):
        return False
    if should_check_fast_path(failure_type, raw_event):
        return True
    return is_clf_shaped_kafka_event(raw_event)
