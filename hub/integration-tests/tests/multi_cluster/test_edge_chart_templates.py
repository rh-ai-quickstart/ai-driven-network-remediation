"""Template tests for the spoke edge Helm chart (C2)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
EDGE_CHART = REPO_ROOT / "edge" / "helm"

_BASE_SETS = [
    "siteId=edge-site-01",
    "namespace=dark-noc-edge",
    "kafka.externalHost=kafka.apps.hub.example.com",
]


def _helm_available() -> bool:
    try:
        subprocess.run(
            ["helm", "version", "--short"],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _helm_available(), reason="helm CLI not available")


def _helm_template(*extra_sets: str) -> str:
    cmd = [
        "helm",
        "template",
        "edge-site-01",
        str(EDGE_CHART),
        *[f"--set={s}" for s in _BASE_SETS],
        *[f"--set={s}" for s in extra_sets],
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, (
        f"helm template failed ({result.returncode}):\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip(), "helm template produced empty output"
    return result.stdout


def _kinds(rendered: str) -> list[str]:
    return re.findall(r"(?m)^kind:\s*(\S+)", rendered)


def test_helm_lint_passes():
    result = subprocess.run(
        ["helm", "lint", str(EDGE_CHART)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_nginx_deployment_has_oom_friendly_memory_limit():
    rendered = _helm_template()
    assert "kind: Deployment" in rendered
    assert "name: edge-nginx" in rendered
    assert "namespace: dark-noc-edge" in rendered
    assert "nginx:1.27-alpine" in rendered
    assert "memory: 64Mi" in rendered
    assert "adnr.io/site-id" in rendered and "edge-site-01" in rendered


def test_clf_forwards_to_hub_kafka_with_mtls_and_site_id():
    rendered = _helm_template()
    assert "kind: ClusterLogForwarder" in rendered
    assert "apiVersion: observability.openshift.io/v1" in rendered
    assert "name: adnr-logcollector" in rendered
    assert "type: kafka" in rendered
    assert "tls://kafka.apps.hub.example.com:443/system-alerts" in rendered
    assert "topic:" in rendered and "system-alerts" in rendered
    assert "secretName:" in rendered and "kafka-client-certs" in rendered
    assert "ca.crt" in rendered
    assert "client.crt" in rendered
    assert "client.key" in rendered
    assert "edge_site_id" in rendered and "edge-site-01" in rendered
    assert "name: keep-warn-error" in rendered
    assert "type: drop" in rendered
    assert "name: edge-application" in rendered
    assert "namespace: dark-noc-edge" in rendered


def test_clf_rbac_and_namespace_present():
    rendered = _helm_template()
    kinds = _kinds(rendered)
    assert "Namespace" in kinds
    assert "ServiceAccount" in kinds
    assert "ClusterRoleBinding" in kinds
    assert "collect-application-logs" in rendered
    assert "name: adnr-logcollector" in rendered


def test_no_hub_components_on_spoke():
    rendered = _helm_template().lower()
    for token in (
        "llamastack",
        "agent-service",
        "mcp-noc",
        "chatbot",
        "pgvector",
        "autorag",
    ):
        assert token not in rendered, f"found hub token {token}"


def test_site_id_required():
    result = subprocess.run(
        [
            "helm",
            "template",
            "edge-site-01",
            str(EDGE_CHART),
            "--set=namespace=dark-noc-edge",
            "--set=kafka.externalHost=kafka.apps.hub.example.com",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "siteId" in (result.stderr + result.stdout)


def test_kafka_external_host_required_when_clf_enabled():
    result = subprocess.run(
        [
            "helm",
            "template",
            "edge-site-01",
            str(EDGE_CHART),
            "--set=siteId=edge-site-01",
            "--set=namespace=dark-noc-edge",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "kafka.externalHost" in (result.stderr + result.stdout)


def test_defaults_render_nginx_only():
    rendered = _helm_template()
    assert "kind: Deployment" in rendered
    assert "name: edge-nginx" in rendered


def test_fast_path_healer_enabled_renders_runner_and_watcher():
    rendered = _helm_template("fastPathHealer.enabled=true")
    assert "name: edge-fast-path-runner" in rendered
    assert "name: edge-fast-path-watcher" in rendered
    assert "EDGE_SITE_ID" in rendered
    assert 'value: "edge-site-01"' in rendered
    assert "kind: NetworkPolicy" in rendered
    assert 'image: "quay.io/rh-ai-quickstart/noc-edge-fast-path-healer:0.1.5"' in rendered
    # runner + watcher each declare the same image ref
    assert rendered.count(
        'image: "quay.io/rh-ai-quickstart/noc-edge-fast-path-healer:0.1.5"'
    ) == 2
    assert "noc-edge-fast-path-healer:0.1.0" not in rendered
    assert "EDGE_COOLDOWN_SECONDS" in rendered
    assert "cpu: 10m" in rendered
    assert "memory: 32Mi" in rendered
    assert "resourceNames" in rendered
    assert "edge-nginx" in rendered


def test_fast_path_healer_disabled_renders_no_runner():
    rendered = _helm_template("fastPathHealer.enabled=false")
    assert "edge-fast-path-runner" not in rendered
