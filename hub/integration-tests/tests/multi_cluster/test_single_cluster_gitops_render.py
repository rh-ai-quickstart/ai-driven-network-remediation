"""Flag matrix for single-cluster / hub-spoke edge GitOps renders (Phase 3).

Combos A–E from .cursor/plans/single-cluster-gitops-parity.md §8 Phase 3.
Offline only: argocd-apply --dry-run + helm template for CLF-off.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SPOKES_GENERATED = REPO_ROOT / "hub" / "helm" / "spokes.generated.yaml"
EDGE_CHART = REPO_ROOT / "edge" / "helm"
DEFAULT_VERSION = "0.1.5"
DEFAULT_REGISTRY = "quay.io/rh-ai-quickstart"


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


def _argocd_dry_run(cluster_count: int, **extra_env: str) -> str:
    _render_spokes(cluster_count)
    env = {
        **os.environ,
        "CLUSTER_COUNT": str(cluster_count),
        "SPOKES_GENERATED": str(SPOKES_GENERATED),
        "GITOPS_REPO_URL": "https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git",
        "GITOPS_REVISION": "main",
        "EDGE_NAMESPACE": "dark-noc-edge",
        "KAFKA_EXTERNAL_HOST": "kafka.apps.hub.example.com",
        "SKIP_OC_CHECK": "1",
        "REGISTRY": DEFAULT_REGISTRY,
        "VERSION": DEFAULT_VERSION,
        **extra_env,
    }
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "acm" / "argocd-apply.sh"), "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    return out


def _count_list_elements(out: str) -> int:
    in_block = False
    count = 0
    for line in out.splitlines():
        if "# SPOKE_ELEMENTS_START" in line:
            in_block = True
            continue
        if "# SPOKE_ELEMENTS_END" in line:
            in_block = False
            continue
        if in_block and re.match(r"^\s*- name:\s+", line):
            count += 1
    return count


def test_flag_matrix_a_defaults_n1_dry_run():
    """A: Defaults N=1 → in-cluster, selfHeal true, CLF off, healer tag=VERSION."""
    out = _argocd_dry_run(1)
    assert "destinationName: in-cluster" in out
    assert "siteId: edge-site-01" in out
    assert "selfHeal: true" in out
    assert 'value: "false"' in out  # clusterLogForwarder.enabled
    assert f"{DEFAULT_REGISTRY}/noc-edge-fast-path-healer" in out
    assert f'value: "{DEFAULT_VERSION}"' in out
    assert _count_list_elements(out) == 1
    assert "destinationName: edge-site-01" not in out
    assert "OK: argocd-apply dry-run" in out


def test_flag_matrix_b_edge_self_heal_false():
    """B: EDGE_SELF_HEAL=false → selfHeal false in render."""
    out = _argocd_dry_run(1, EDGE_SELF_HEAL="false")
    assert "selfHeal: false" in out
    assert "selfHeal: true" not in out
    assert "destinationName: in-cluster" in out


def test_flag_matrix_c_registry_version_override():
    """C: REGISTRY/VERSION override → healer image repository/tag."""
    out = _argocd_dry_run(
        1,
        REGISTRY="quay.io/custom-lab",
        VERSION="9.9.9-test",
    )
    assert "quay.io/custom-lab/noc-edge-fast-path-healer" in out
    assert 'value: "9.9.9-test"' in out
    assert f"{DEFAULT_REGISTRY}/noc-edge-fast-path-healer" not in out


def test_flag_matrix_d_clf_disabled_omits_clf_kind():
    """D: clusterLogForwarder.enabled=false → no ClusterLogForwarder kind."""
    if not _helm_available():
        pytest.skip("helm CLI not available")
    cmd = [
        "helm",
        "template",
        "edge-site-01",
        str(EDGE_CHART),
        "--set=siteId=edge-site-01",
        "--set=namespace=dark-noc-edge",
        "--set=clusterLogForwarder.enabled=false",
        "--set=fastPathHealer.enabled=true",
        f"--set=fastPathHealer.image.repository={DEFAULT_REGISTRY}/noc-edge-fast-path-healer",
        f"--set=fastPathHealer.image.tag={DEFAULT_VERSION}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    rendered = result.stdout
    assert "kind: ClusterLogForwarder" not in rendered
    assert "adnr-logcollector" not in rendered
    assert "name: edge-nginx" in rendered
    assert f'image: "{DEFAULT_REGISTRY}/noc-edge-fast-path-healer:{DEFAULT_VERSION}"' in rendered


def test_flag_matrix_e_n2_regression_no_in_cluster_element():
    """E: N=2 regression → 2 spoke dests, no in-cluster list element."""
    out = _argocd_dry_run(2)
    assert _count_list_elements(out) == 2
    assert "destinationName: edge-site-01" in out
    assert "destinationName: edge-site-02" in out
    assert "destinationName: in-cluster" not in out
    assert 'value: "true"' in out  # clusterLogForwarder.enabled for MC
    assert "selfHeal: true" in out
    assert f'value: "{DEFAULT_VERSION}"' in out
    assert "OK: argocd-apply dry-run" in out
