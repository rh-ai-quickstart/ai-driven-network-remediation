"""Loki releases must not share ownership of cluster or logging-namespace resources."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

LOKISTACK_CHART = Path(__file__).resolve().parents[4] / "hub/helm/lib-charts/lokistack"
SHARED_RESOURCE_KINDS = {"ClusterRole", "ClusterRoleBinding", "ClusterLogForwarder"}
LOGGING_NAMESPACE = "openshift-logging"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI not available")


def _render(namespace, release, *helm_args):
    return subprocess.run(
        ["helm", "template", release, str(LOKISTACK_CHART), "--namespace", namespace, *helm_args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _shared_resource_names(rendered):
    shared_names = set()
    for document in rendered.split("---"):
        resource = re.search(r"^kind: (\S+)\nmetadata:\n  name: (\S+)", document, re.MULTILINE)
        if resource is None:
            continue
        kind, name = resource.groups()
        if kind in SHARED_RESOURCE_KINDS or f"  namespace: {LOGGING_NAMESPACE}\n" in document:
            shared_names.add((kind, name))
    return shared_names


@pytest.mark.parametrize("namespace,release", [("hub-beta", "hub"), ("hub-alpha", "other")])
def test_shared_resources_do_not_collide(namespace, release):
    original_names = _shared_resource_names(_render("hub-alpha", "hub"))
    other_names = _shared_resource_names(_render(namespace, release))
    assert original_names and other_names
    assert original_names.isdisjoint(other_names)


def test_reader_bindings_and_collector_reference_their_release_resources():
    namespace, release = "hub-alpha", "hub"
    rendered = _render(namespace, release)
    for reader in ("grafana-loki-reader", "mcp-lokistack-reader"):
        role_name = f"{reader}-{namespace}-{release}"
        assert f"kind: ClusterRole\nmetadata:\n  name: {role_name}\n" in rendered
        assert f"kind: ClusterRoleBinding\nmetadata:\n  name: {role_name}\n" in rendered
        assert f"  kind: ClusterRole\n  name: {role_name}\n" in rendered
        assert f"    name: {reader}\n    namespace: {namespace}\n" in rendered
    collector_name = f"logcollector-{namespace}-{release}"
    assert f"kind: ServiceAccount\nmetadata:\n  name: {collector_name}\n" in rendered
    assert f"  serviceAccount:\n    name: {collector_name}\n" in rendered
    assert f"    name: {collector_name}\n    namespace: {LOGGING_NAMESPACE}\n" in rendered


def test_collector_does_not_restrict_application_namespaces():
    rendered = _render("hub-alpha", "hub")
    assert "inputRefs: [application]" in rendered
    assert "includes:" not in rendered
    assert "inputRefs: [infrastructure]" not in rendered


def test_infrastructure_only_collector_has_no_application_input():
    rendered = _render(
        "hub-alpha",
        "hub",
        "--set",
        "clusterLogForwarder.pipelines.application=false,clusterLogForwarder.pipelines.infrastructure=true",
    )
    assert "inputRefs: [infrastructure]" in rendered
    assert "inputRefs: [application]" not in rendered
