"""Unit tests for CLUSTER_COUNT topology helpers and spoke rendering."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOPOLOGY_DIR = REPO_ROOT / "scripts" / "topology"


def _load(name: str, filename: str):
    path = TOPOLOGY_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lib = _load("adnr_topology_lib", "lib.py")
validate_mod = _load("adnr_topology_validate", "validate.py")


@pytest.mark.parametrize(
    ("cluster_count", "mode", "spoke_count"),
    [
        (1, "single-cluster", 0),
        (2, "hub-spoke", 2),
        (3, "hub-spoke", 3),
    ],
)
def test_cluster_count_semantics(cluster_count, mode, spoke_count):
    assert lib.deployment_mode_for(cluster_count) == mode
    assert lib.spoke_count_for(cluster_count) == spoke_count


def test_cluster_count_rejects_zero():
    with pytest.raises(ValueError, match="CLUSTER_COUNT"):
        lib.spoke_count_for(0)


def test_build_spokes_names_and_site_ids():
    spokes = lib.build_spokes(2, prefix="edge-site", namespace="dark-noc-edge")
    assert spokes == [
        {
            "name": "edge-site-01",
            "siteId": "edge-site-01",
            "namespace": "dark-noc-edge",
        },
        {
            "name": "edge-site-02",
            "siteId": "edge-site-02",
            "namespace": "dark-noc-edge",
        },
    ]


def test_build_spokes_empty_for_single_cluster():
    assert lib.build_spokes(1) == []


def test_render_values_yaml_single_cluster():
    text = lib.render_values_yaml(1)
    assert "deploymentMode: single-cluster" in text
    assert "spokeCount: 0" in text
    assert "spokes: []" in text
    assert "edge-site-01" not in text
    assert "mcp-servers:" not in text


def test_render_values_yaml_two_spokes():
    text = lib.render_values_yaml(2)
    assert "deploymentMode: hub-spoke" in text
    assert "spokeCount: 2" in text
    assert "name: edge-site-01" in text
    assert "siteId: edge-site-01" in text
    assert "name: edge-site-02" in text
    assert "siteId: edge-site-02" in text
    assert "secretName: noc-openshift-kubeconfig-edge-site-01" in text
    assert "secretName: noc-openshift-kubeconfig-edge-site-02" in text
    assert "mountPath: /kubeconfigs/edge-site-01" in text
    assert 'KUBECONFIG_DIR: "/kubeconfigs"' in text
    assert 'DEPLOYMENT_MODE: "hub-spoke"' in text
    assert "serviceAccountName: mcp-noc-openshift" in text


def test_validate_single_cluster_ok():
    ok, messages, summary = validate_mod.validate_topology(cluster_count=1)
    assert ok is True
    assert summary["deploymentMode"] == "single-cluster"
    assert summary["spokeCount"] == 0
    assert any("OK:" in m for m in messages)


def test_validate_hub_spoke_requires_gitops():
    ok, messages, _ = validate_mod.validate_topology(
        cluster_count=2,
        gitops_repo_url="",
        gitops_revision="",
        skip_oc_check=True,
    )
    assert ok is False
    assert any("GITOPS_REPO_URL" in m for m in messages)


def test_validate_hub_spoke_ok_with_skip_oc():
    ok, messages, summary = validate_mod.validate_topology(
        cluster_count=2,
        gitops_repo_url="https://github.com/example/repo.git",
        gitops_revision="main",
        skip_oc_check=True,
    )
    assert ok is True
    assert summary["spokeCount"] == 2
    assert summary["spokes"][0]["name"] == "edge-site-01"
    assert summary["spokes"][1]["name"] == "edge-site-02"
    assert any("OK:" in m for m in messages)


def test_build_spokes_rejects_unsafe_namespace():
    with pytest.raises(ValueError, match="EDGE_NAMESPACE"):
        lib.build_spokes(2, namespace="dark noc")


def test_render_values_yaml_rejects_unsafe_prefix():
    with pytest.raises(ValueError, match="SPOKE_NAME_PREFIX"):
        lib.render_values_yaml(2, prefix="edge:site")
