"""Static template checks for ACM placement, policy, and Hive ClusterDeployment."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ACM_DIR = REPO_ROOT / "cross-cluster" / "acm"


def _read(name: str) -> str:
    path = ACM_DIR / name
    assert path.is_file(), f"missing ACM manifest: {path}"
    return path.read_text(encoding="utf-8")


def _kinds(text: str) -> list[str]:
    return re.findall(r"(?m)^kind:\s*(\S+)", text)


def test_placement_selects_edge_role_label():
    text = _read("placement.yaml")
    kinds = _kinds(text)
    assert "ManagedClusterSet" in kinds
    assert "ManagedClusterSetBinding" in kinds
    assert "Placement" in kinds
    assert "name: adnr-edge" in text
    assert "name: adnr-edge-spokes" in text
    assert "namespace: __NAMESPACE__" in text
    assert "adnr.io/role: edge" in text
    assert "clusterSets:" in text
    assert "- adnr-edge" in text
    # ACM 2.17 rejects LabelSelector ManagedClusterSets; exclusive label membership.
    assert "selectorType: ExclusiveClusterSetLabel" in text
    assert "selectorType: LabelSelector" not in text
    # Must be substituted by apply-placement.sh (never bake hub).
    assert "namespace: hub" not in text


def test_gitopscluster_registers_placement_spokes():
    text = _read("gitopscluster.yaml")
    kinds = _kinds(text)
    assert "GitOpsCluster" in kinds
    assert "name: adnr-edge" in text
    assert "namespace: __NAMESPACE__" in text
    assert "argoNamespace: __ARGOCD_NAMESPACE__" in text
    assert "cluster: __ACM_HUB_CLUSTER__" in text
    assert "name: adnr-edge-spokes" in text
    assert "kind: Placement" in text
    assert "local-cluster" not in text
    assert "openshift-gitops" not in text


def test_namespace_policy_enforces_edge_namespace_placeholder():
    text = _read("namespace-policy.yaml")
    kinds = _kinds(text)
    assert "Policy" in kinds
    assert "PlacementBinding" in kinds
    assert "name: adnr-edge-namespace" in text
    assert "name: __EDGE_NAMESPACE__" in text
    assert "namespace: __NAMESPACE__" in text
    assert "adnr.io/role: edge" in text
    assert "kind: Placement" in text
    assert "name: adnr-edge-spokes" in text
    assert "remediationAction: enforce" in text
    assert "name: dark-noc-edge" not in text
    assert "namespace: hub" not in text


def test_clusterdeployment_template_placeholders():
    text = _read("clusterdeployment.yaml")
    kinds = _kinds(text)
    assert "Namespace" in kinds
    assert "ClusterDeployment" in kinds
    assert "MachinePool" in kinds
    for token in (
        "__SPOKE_NAME__",
        "__SITE_ID__",
        "__BASE_DOMAIN__",
        "__IMAGE_SET__",
        "__AWS_REGION__",
    ):
        assert token in text, f"missing placeholder {token}"
    assert "adnr.io/role: edge" in text
    assert "adnr.io/site-id: __SITE_ID__" in text
    assert "cluster.open-cluster-management.io/clusterset: adnr-edge" in text
    # Must not ship a literal spoke name; create-clusters.sh substitutes.
    assert "edge-site-01" not in text
