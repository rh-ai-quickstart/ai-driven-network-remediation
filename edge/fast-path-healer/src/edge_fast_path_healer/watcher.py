from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx
from kubernetes import client

from edge_fast_path_healer.detect import (
    deployment_memory_limit_mi,
    pod_oom_event_key,
    unsafe_limit_event_key,
)
from edge_fast_path_healer.k8s_client import load_k8s_apps_api
from edge_fast_path_healer.logging_util import log_event
from edge_fast_path_healer.models import RemediationEvent


@dataclass(frozen=True)
class WatcherSettings:
    namespace: str
    deployment: str
    label_selector: str
    site_id: str
    runner_url: str
    unsafe_memory_limit_mi: int
    poll_interval_seconds: int
    cooldown_seconds: int


def settings_from_env() -> WatcherSettings:
    return WatcherSettings(
        namespace=os.environ["EDGE_NAMESPACE"],
        deployment=os.environ["EDGE_DEPLOYMENT"],
        label_selector=os.environ.get("EDGE_LABEL_SELECTOR", f"app={os.environ['EDGE_DEPLOYMENT']}"),
        site_id=os.environ["EDGE_SITE_ID"],
        runner_url=os.environ["EDGE_RUNNER_URL"],
        unsafe_memory_limit_mi=int(os.environ.get("EDGE_UNSAFE_MEMORY_LIMIT_MI", "32")),
        poll_interval_seconds=int(os.environ.get("EDGE_POLL_INTERVAL_SECONDS", "10")),
        cooldown_seconds=int(os.environ.get("EDGE_COOLDOWN_SECONDS", "300")),
    )


def post_event(client: httpx.Client, url: str, event: RemediationEvent) -> None:
    response = client.post(url, json=event.model_dump(), timeout=10.0)
    response.raise_for_status()
    log_event(
        "watcher",
        site_id=event.site_id,
        posted=True,
        status_code=response.status_code,
        event=event.model_dump(),
    )


def scan_once(
    apps_api: client.AppsV1Api,
    core_api: client.CoreV1Api,
    settings: WatcherSettings,
    seen: set[str],
    http_client: httpx.Client,
) -> None:
    dep_obj = apps_api.read_namespaced_deployment(name=settings.deployment, namespace=settings.namespace)
    dep = client.ApiClient().sanitize_for_serialization(dep_obj)
    limit_mi = deployment_memory_limit_mi(dep)
    unsafe_key = unsafe_limit_event_key(limit_mi, settings.unsafe_memory_limit_mi)
    if unsafe_key and unsafe_key not in seen:
        event = RemediationEvent(
            failure_type="OOMKilled",
            namespace=settings.namespace,
            deployment=settings.deployment,
            site_id=settings.site_id,
            reason=f"unsafe-memory-limit-{limit_mi}Mi",
        )
        post_event(http_client, settings.runner_url, event)
        seen.add(unsafe_key)

    pods = core_api.list_namespaced_pod(namespace=settings.namespace, label_selector=settings.label_selector)
    for item in pods.items:
        pod = client.ApiClient().sanitize_for_serialization(item)
        key = pod_oom_event_key(pod, max_age_seconds=settings.cooldown_seconds)
        if key and key not in seen:
            event = RemediationEvent(
                failure_type="OOMKilled",
                namespace=settings.namespace,
                deployment=settings.deployment,
                site_id=settings.site_id,
                pod=pod.get("metadata", {}).get("name"),
            )
            post_event(http_client, settings.runner_url, event)
            seen.add(key)


def main() -> None:
    settings = settings_from_env()
    apps_api = load_k8s_apps_api()
    core_api = client.CoreV1Api()
    seen: set[str] = set()
    log_event("watcher", site_id=settings.site_id, status="started", runner_url=settings.runner_url)
    with httpx.Client() as http_client:
        while True:
            try:
                scan_once(apps_api, core_api, settings, seen, http_client)
            except Exception as exc:
                log_event("watcher", site_id=settings.site_id, status="error", error=str(exc))
            time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
