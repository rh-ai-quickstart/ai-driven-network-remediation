from __future__ import annotations

from kubernetes import client, config


def load_k8s_apps_api() -> client.AppsV1Api:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.AppsV1Api()
