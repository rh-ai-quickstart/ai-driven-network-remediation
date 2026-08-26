"""Static template checks for ArgoCD AppProject and ApplicationSet."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ARGOCD_DIR = REPO_ROOT / "cross-cluster" / "argocd"


def _read(name: str) -> str:
    path = ARGOCD_DIR / name
    assert path.is_file(), f"missing ArgoCD manifest: {path}"
    return path.read_text(encoding="utf-8")


def _kinds(text: str) -> list[str]:
    return re.findall(r"(?m)^kind:\s*(\S+)", text)


def test_appproject_restricts_edge_destinations_and_resources():
    text = _read("project.yaml")
    assert _kinds(text) == ["AppProject"]
    assert "name: adnr-edge" in text
    assert "namespace: openshift-gitops" in text
    assert "namespace: dark-noc-edge" in text
    assert "kind: Namespace" in text
    assert "kind: ClusterRoleBinding" in text
    assert "kind: ClusterLogForwarder" in text
    assert "kind: Deployment" in text
    assert "kind: NetworkPolicy" in text
    assert "sourceRepos:" in text
    assert 'name: "*"' in text


def test_applicationset_list_generator_and_site_id_params():
    text = _read("applicationset-edge.yaml")
    assert "kind: ApplicationSet" in text
    assert "name: adnr-edge" in text
    assert "namespace: openshift-gitops" in text
    assert "SPOKE_ELEMENTS_START" in text
    assert "SPOKE_ELEMENTS_END" in text
    assert "name: edge-site-01" in text
    assert "siteId: edge-site-01" in text
    assert "name: edge-site-02" in text
    assert "siteId: edge-site-02" in text
    assert "path: edge/helm" in text
    assert "name: siteId" in text
    assert 'value: "{{.siteId}}"' in text
    assert "name: kafka.externalHost" in text
    assert "__KAFKA_EXTERNAL_HOST__" in text
    assert 'name: "{{.name}}"' in text
    assert "resources-finalizer.argocd.argoproj.io" in text
    assert "project: adnr-edge" in text
    assert "RespectIgnoreDifferences=true" in text
    assert "name: edge-nginx" in text
    assert "/spec/template/spec/containers/0/resources" in text
    assert "/metadata/annotations" in text
    assert "/spec/template/metadata/annotations" in text
