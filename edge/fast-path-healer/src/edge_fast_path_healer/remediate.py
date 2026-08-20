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


def cooldown_active(deployment: dict, cooldown_seconds: int) -> bool:
    annotations = deployment.get("metadata", {}).get("annotations") or {}
    raw = annotations.get(ANNOTATION_LAST_HEAL)
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
    return age.total_seconds() < cooldown_seconds


def build_restart_patch(memory_request: str, memory_limit: str, restarted_at: str) -> dict:
    return {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": restarted_at,
                        ANNOTATION_LAST_HEAL: restarted_at,
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "resources": {
                                "requests": {"memory": memory_request},
                                "limits": {"memory": memory_limit},
                            }
                        }
                    ]
                },
            }
        }
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

    patch = [
        {
            "op": "replace",
            "path": "/spec/template/spec/containers/0/resources/requests/memory",
            "value": memory_request,
        },
        {
            "op": "replace",
            "path": "/spec/template/spec/containers/0/resources/limits/memory",
            "value": memory_limit,
        },
        {
            "op": "add",
            "path": "/spec/template/metadata/annotations",
            "value": {
                "kubectl.kubernetes.io/restartedAt": ts,
                ANNOTATION_LAST_HEAL: ts,
                ANNOTATION_SITE: site_id,
            },
        },
    ]
    try:
        api.patch_namespaced_deployment(
            name=deployment,
            namespace=namespace,
            body=patch,
        )
    except client.exceptions.ApiException as exc:
        if exc.status == 422 and "already exists" in (exc.body or ""):
            patch[2] = {
                "op": "replace",
                "path": "/spec/template/metadata/annotations/kubectl.kubernetes.io~1restartedAt",
                "value": ts,
            }
            api.patch_namespaced_deployment(name=deployment, namespace=namespace, body=patch)
        else:
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
