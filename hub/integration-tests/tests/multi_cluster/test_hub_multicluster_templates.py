"""Helm template tests for hub multi-cluster creds vs single-cluster edgeRbac."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
HUB_CHART = REPO_ROOT / "hub" / "helm"
TOPOLOGY_LIB = REPO_ROOT / "scripts" / "topology" / "lib.py"


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


def _render_spokes_values(cluster_count: int) -> Path:
    spec = importlib.util.spec_from_file_location("adnr_topology_lib_c8", TOPOLOGY_LIB)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    text = mod.render_values_yaml(cluster_count)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        prefix=f"spokes-c{cluster_count}-",
        delete=False,
        encoding="utf-8",
    )
    tmp.write(text)
    tmp.close()
    return Path(tmp.name)


def _helm_template(values_file: Path, *extra_sets: str) -> str:
    cmd = [
        "helm",
        "template",
        "hub",
        str(HUB_CHART),
        "--namespace",
        "hub",
        "-f",
        str(values_file),
        *[f"--set={s}" for s in extra_sets],
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, (
        f"helm template failed ({result.returncode}):\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip(), "helm template produced empty output"
    return result.stdout


def test_hub_spoke_renders_multi_cluster_creds_job():
    values = _render_spokes_values(2)
    try:
        rendered = _helm_template(values)
    finally:
        values.unlink(missing_ok=True)

    assert "templates/multi-cluster-creds-job.yaml" in rendered
    assert "multi-cluster-creds" in rendered
    assert "mcp-noc-openshift" in rendered
    assert "edge-site-01" in rendered
    assert "edge-site-02" in rendered
    assert "noc-openshift-kubeconfig-edge-site-01" in rendered
    assert "noc-openshift-kubeconfig-edge-site-02" in rendered
    assert "managedclusters" in rendered
    assert "insecure-skip-tls-verify:" in rendered
    assert "edge-site-01-admin-kubeconfig" in rendered
    assert "edge-site-02-admin-kubeconfig" in rendered
    assert "resourceNames:" in rendered
    assert "kind: ClusterPermission" in rendered
    assert "rbac.open-cluster-management.io" in rendered
    assert rendered.count("kind: ClusterPermission") >= 2
    assert "pre-install,pre-upgrade" in rendered
    assert "cluster:hub:system:serviceaccount:hub:mcp-noc-openshift" in rendered
    # Name-scoped Hive secret gets only (no unrestricted secrets list rule).
    secrets_blocks = [
        block
        for block in rendered.split("- apiGroups:")
        if 'resources: ["secrets"]' in block or 'resources: ["secrets"]' in block
    ]
    assert secrets_blocks, "expected secrets RBAC rules in rendered manifests"
    for block in secrets_blocks:
        if "resourceNames:" in block:
            assert 'verbs: ["get"]' in block or 'verbs: ["get"]' in block
            assert 'verbs: ["get", "list"]' not in block
    # Hub-spoke must not emit the same-cluster edge RBAC hook.
    assert "templates/edge-rbac-job.yaml" not in rendered
    assert "templates/edge-rbac-sa.yaml" not in rendered


def test_aap_multicluster_rbac_renders_per_spoke():
    values = _render_spokes_values(2)
    try:
        rendered = _helm_template(
            values,
            "network.aapCredential.enabled=true",
            "network.aapCredential.multicluster.enabled=true",
        )
    finally:
        values.unlink(missing_ok=True)

    assert "templates/aap-multicluster-rbac.yaml" in rendered
    assert rendered.count("kind: ClusterPermission") >= 2
    assert "cluster:hub:system:serviceaccount:aap:aap-integration-serviceaccount" in rendered
    assert "edge-site-01" in rendered
    assert "edge-site-02" in rendered
    assert 'kind: User' in rendered


def test_aap_multicluster_rbac_skipped_without_hub_spoke():
    values = _render_spokes_values(1)
    try:
        rendered = _helm_template(
            values,
            "network.aapCredential.enabled=true",
            "network.aapCredential.multicluster.enabled=true",
        )
    finally:
        values.unlink(missing_ok=True)

    assert "templates/aap-multicluster-rbac.yaml" not in rendered


def test_lokistack_logcollector_rbac():
    values = _render_spokes_values(1)
    try:
        rendered = _helm_template(values, "network.lokistack.enabled=true")
    finally:
        values.unlink(missing_ok=True)

    assert "kind: ServiceAccount" in rendered
    assert "name: logcollector" in rendered
    assert "collect-application-logs" in rendered
    assert "collect-infrastructure-logs" in rendered
    assert "logging-collector-logs-writer" in rendered
    assert rendered.count("kind: ClusterRoleBinding") >= 3


def test_single_cluster_omits_multi_cluster_creds_job():
    values = _render_spokes_values(1)
    try:
        rendered = _helm_template(values)
    finally:
        values.unlink(missing_ok=True)

    assert "templates/multi-cluster-creds-job.yaml" not in rendered
    assert "multi-cluster-creds-setup" not in rendered


def test_hub_spoke_disables_edge_rbac_job_even_when_enabled():
    values = _render_spokes_values(2)
    try:
        rendered = _helm_template(values, "network.edgeRbac.enabled=true")
    finally:
        values.unlink(missing_ok=True)

    assert "templates/multi-cluster-creds-job.yaml" in rendered
    assert "templates/edge-rbac-job.yaml" not in rendered
    assert "templates/edge-rbac-sa.yaml" not in rendered
