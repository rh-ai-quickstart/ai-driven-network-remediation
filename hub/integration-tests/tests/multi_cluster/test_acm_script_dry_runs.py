"""Offline dry-run checks for ACM create-clusters and argocd-apply scripts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SPOKES_GENERATED = REPO_ROOT / "hub" / "helm" / "spokes.generated.yaml"


def _render_spokes(cluster_count: int) -> None:
    env = {
        **os.environ,
        "CLUSTER_COUNT": str(cluster_count),
        "EDGE_NAMESPACE": "dark-noc-edge",
        "SPOKE_NAME_PREFIX": "edge-site",
    }
    result = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "scripts" / "topology" / "render-spokes.py"),
            "-o",
            str(SPOKES_GENERATED),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert SPOKES_GENERATED.is_file()


def _run_script(script: str, *args: str, cluster_count: int, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "CLUSTER_COUNT": str(cluster_count),
        "SPOKES_GENERATED": str(SPOKES_GENERATED),
        "GITOPS_REPO_URL": "https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git",
        "GITOPS_REVISION": "main",
        "EDGE_NAMESPACE": "dark-noc-edge",
        "KAFKA_EXTERNAL_HOST": "kafka.apps.hub.example.com",
        "SKIP_OC_CHECK": "1",
        **extra_env,
    }
    return subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "acm" / script), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_create_clusters_dry_run_renders_two_clusterdeployments():
    _render_spokes(2)
    result = _run_script("create-clusters.sh", "--dry-run", cluster_count=2)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert out.count("kind: ClusterDeployment") == 2
    assert "edge-site-01" in out
    assert "edge-site-02" in out
    assert "adnr.io/site-id: edge-site-01" in out
    assert "adnr.io/site-id: edge-site-02" in out
    assert "__SPOKE_NAME__" not in out
    assert "OK: acm-create-clusters dry-run" in out


def test_create_clusters_dry_run_skips_single_cluster():
    result = _run_script("create-clusters.sh", "--dry-run", cluster_count=1)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "SKIP:" in out
    assert "single-cluster" in out


def test_argocd_apply_dry_run_renders_appproject_and_appset():
    _render_spokes(2)
    result = _run_script("argocd-apply.sh", "--dry-run", cluster_count=2)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "kind: AppProject" in out
    assert "kind: ApplicationSet" in out
    assert out.count("name: edge-site-01") >= 1
    assert out.count("name: edge-site-02") >= 1
    assert "siteId: edge-site-01" in out
    assert "kafka.apps.hub.example.com" in out
    assert "ADNR_KAFKA_EXTERNAL_HOST" not in out
    assert "__KAFKA_EXTERNAL_HOST__" not in out
    assert "OK: argocd-apply dry-run" in out


def test_argocd_apply_skips_single_cluster():
    result = _run_script("argocd-apply.sh", "--dry-run", cluster_count=1)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "SKIP:" in out
    assert "single-cluster" in out


def test_prereq_check_skips_single_cluster():
    result = _run_script("prereq-check.sh", cluster_count=1)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "SKIP:" in out or "single-cluster" in out.lower()


def test_apply_placement_dry_run_substitutes_namespace_and_hub():
    _render_spokes(2)
    result = _run_script(
        "apply-placement.sh",
        "--dry-run",
        cluster_count=2,
        NAMESPACE="adnr-hub",
        EDGE_NAMESPACE="edge-workloads",
        ACM_HUB_CLUSTER="my-hub",
        ARGOCD_NAMESPACE="openshift-gitops",
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "namespace: adnr-hub" in out
    assert "name: edge-workloads" in out
    assert "cluster: my-hub" in out
    assert "argoNamespace: openshift-gitops" in out
    assert "__NAMESPACE__" not in out
    assert "__EDGE_NAMESPACE__" not in out
    assert "__ACM_HUB_CLUSTER__" not in out
    assert "OK: apply-placement dry-run" in out


def test_apply_placement_skips_single_cluster():
    result = _run_script("apply-placement.sh", "--dry-run", cluster_count=1)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "SKIP:" in out
    assert "single-cluster" in out
