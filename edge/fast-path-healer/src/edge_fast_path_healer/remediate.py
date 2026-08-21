from __future__ import annotations

from datetime import datetime, timezone

from kubernetes import client

from edge_fast_path_healer.logging_util import log_event
from edge_fast_path_healer.models import RemediationResult

ANNOTATION_LAST_HEAL = "adnr.io/fast-path-last-heal"
ANNOTATION_SITE = "adnr.io/fast-path-site"


def parse_memory_mi(value: str) -> int | None:
    if not value:
        return None
    if value.endswith("Mi"):
        return int(value[:-2])
    if value.endswith("Gi"):
        return int(value[:-2]) * 1024
    return None


def _annotation_maps(deployment: dict) -> tuple[dict, dict]:
    meta = (deployment.get("metadata") or {}).get("annotations") or {}
    tpl = (
        ((deployment.get("spec") or {}).get("template") or {}).get("metadata") or {}
    ).get("annotations") or {}
    return meta, tpl


def cooldown_active(deployment: dict, cooldown_seconds: int) -> bool:
    meta, tpl = _annotation_maps(deployment)
    raw = meta.get(ANNOTATION_LAST_HEAL) or tpl.get(ANNOTATION_LAST_HEAL)
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
    return age.total_seconds() < cooldown_seconds


def build_restart_patch(
    memory_request: str,
    memory_limit: str,
    restarted_at: str,
    site_id: str = "",
    container_name: str = "nginx",
    extra_resources: dict | None = None,
) -> dict:
    requests = {"memory": memory_request}
    limits = {"memory": memory_limit}
    current = extra_resources or {}
    if (current.get("requests") or {}).get("cpu"):
        requests["cpu"] = current["requests"]["cpu"]
    if (current.get("limits") or {}).get("cpu"):
        limits["cpu"] = current["limits"]["cpu"]
    heal_annotations = {
        ANNOTATION_LAST_HEAL: restarted_at,
        ANNOTATION_SITE: site_id,
    }
    return {
        "metadata": {"annotations": heal_annotations},
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        **heal_annotations,
                        "kubectl.kubernetes.io/restartedAt": restarted_at,
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": container_name,
                            "resources": {"requests": requests, "limits": limits},
                        }
                    ]
                },
            }
        },
    }


def remediate_oom(
    api: client.AppsV1Api,
    *,
    namespace: str,
    deployment: str,
    site_id: str,
    memory_request: str,
    memory_limit: str,
    cooldown_seconds: int,
) -> RemediationResult:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dep_obj = api.read_namespaced_deployment(name=deployment, namespace=namespace)
    dep = client.ApiClient().sanitize_for_serialization(dep_obj)

    if cooldown_active(dep, cooldown_seconds):
        msg = "cooldown active; skipping remediation"
        log_event("runner", site_id=site_id, action="local_fast_path_restart", result="skipped", message=msg)
        return RemediationResult(
            site_id=site_id,
            namespace=namespace,
            deployment=deployment,
            result="skipped",
            timestamp=datetime.now(timezone.utc),
            message=msg,
        )

    containers = ((dep.get("spec") or {}).get("template") or {}).get("spec", {}).get("containers") or []
    container_name = (containers[0].get("name") if containers else None) or "nginx"
    extra_resources = (containers[0].get("resources") if containers else None) or {}
    patch = build_restart_patch(
        memory_request,
        memory_limit,
        ts,
        site_id=site_id,
        container_name=container_name,
        extra_resources=extra_resources,
    )
    try:
        api.patch_namespaced_deployment(
            name=deployment,
            namespace=namespace,
            body=patch,
        )
    except client.exceptions.ApiException as exc:
        log_event("runner", site_id=site_id, action="local_fast_path_restart", result="failed", error=str(exc))
        return RemediationResult(
            site_id=site_id,
            namespace=namespace,
            deployment=deployment,
            result="failed",
            timestamp=datetime.now(timezone.utc),
            message=str(exc),
        )

    log_event(
        "runner",
        site_id=site_id,
        namespace=namespace,
        deployment=deployment,
        action="local_fast_path_restart",
        result="success",
        memory_request=memory_request,
        memory_limit=memory_limit,
    )
    return RemediationResult(
        site_id=site_id,
        namespace=namespace,
        deployment=deployment,
        result="success",
        timestamp=datetime.now(timezone.utc),
        message="deployment patched",
    )
