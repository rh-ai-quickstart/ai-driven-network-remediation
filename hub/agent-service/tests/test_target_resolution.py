from unittest.mock import AsyncMock

import pytest
from helpers import make_log_event, selected_state, update_selection

from agent_service.target_resolution import (
    resolve_selected_target,
    resolve_target_with_lookup,
)


def test_preserves_explicit_namespace_on_a_different_resource_and_cluster():
    state = selected_state(site="selected-site", namespace="selected-namespace")
    update_selection(state, target={"kind": "CustomResource"})
    selected = state.root_cause_analysis.selected_action

    assert resolve_selected_target(selected, []) is selected


def test_accepts_explicitly_cluster_scoped_custom_resource():
    state = selected_state(namespace="")
    update_selection(state, target={"kind": "ClusterConfiguration", "cluster_scoped": True})
    selected = state.root_cause_analysis.selected_action

    assert resolve_selected_target(selected, []) is selected


@pytest.mark.parametrize("namespace", ["", " "])
def test_empty_namespace_does_not_implicitly_mean_cluster_scope(namespace):
    state = selected_state(namespace=namespace)
    update_selection(state, target={"kind": "CustomResource"})

    with pytest.raises(ValueError, match="namespace is missing"):
        resolve_selected_target(state.root_cause_analysis.selected_action, [])


@pytest.mark.parametrize(
    "kind, namespace",
    [("Deployment", ""), ("deployment", ""), ("DaemonSet", ""), ("CustomResource", "prod")],
)
def test_rejects_conflicting_cluster_scope_declaration(kind, namespace):
    state = selected_state(namespace=namespace)
    update_selection(state, target={"kind": kind, "cluster_scoped": True})
    observations = [{"edge_site_id": "edge-1", "deployment": "orders-api", "namespace": "prod"}]

    with pytest.raises(ValueError, match="conflicting namespace and cluster-scoped identity"):
        resolve_selected_target(state.root_cause_analysis.selected_action, observations)


def test_duplicate_observations_do_not_make_namespace_ambiguous_or_mutate_selection():
    state = selected_state(namespace="")
    selected = state.root_cause_analysis.selected_action
    observations = [{"edge_site_id": "edge-1", "deployment": "orders-api", "namespace": "prod"}] * 2

    resolved = resolve_selected_target(selected, observations)

    assert resolved.target.namespace == "prod"
    assert selected.target.namespace == ""


@pytest.mark.parametrize("namespace", [None, "", " "])
def test_ignores_observations_without_usable_namespace(namespace):
    state = selected_state(namespace="")
    observations = [{"edge_site_id": "edge-1", "deployment": "orders-api", "namespace": namespace}]

    with pytest.raises(ValueError, match="namespace is missing"):
        resolve_selected_target(state.root_cause_analysis.selected_action, observations)


async def test_alert_namespace_is_only_a_hint_until_exact_deployment_is_confirmed():
    selected = selected_state(namespace="", deployment="microservice-b").root_cause_analysis.selected_action
    lookup = AsyncMock(return_value={"success": True, "deployment": "microservice-b", "namespace": "edge-workloads"})

    resolved = await resolve_target_with_lookup(selected, [], make_log_event(namespace="edge-workloads"), lookup)

    assert resolved.target.namespace == "edge-workloads"
    assert selected.target.namespace == ""
    lookup.assert_awaited_once_with(
        "get_deployment", {"deployment": "microservice-b", "namespace": "edge-workloads", "edge_site_id": "edge-1"}
    )


@pytest.mark.parametrize("namespace, observations", [("explicit", []), ("", [{"namespace": "observed"}])])
async def test_existing_target_evidence_never_triggers_lookup(namespace, observations):
    selected = selected_state(namespace=namespace).root_cause_analysis.selected_action
    observations = [{"edge_site_id": "edge-1", "deployment": "orders-api", **entry} for entry in observations]
    lookup = AsyncMock()

    resolved = await resolve_target_with_lookup(selected, observations, make_log_event(namespace="hint"), lookup)

    assert resolved.target.namespace == (namespace or "observed")
    lookup.assert_not_called()


async def test_fresh_lookup_cannot_disambiguate_conflicting_target_observations():
    selected = selected_state(namespace="").root_cause_analysis.selected_action
    observations = [
        {"edge_site_id": "edge-1", "deployment": "orders-api", "namespace": namespace}
        for namespace in ("team-a", "team-b")
    ]
    lookup = AsyncMock()

    with pytest.raises(ValueError, match="ambiguous"):
        await resolve_target_with_lookup(selected, observations, make_log_event(namespace="team-a"), lookup)

    lookup.assert_not_called()


@pytest.mark.parametrize("log_event", [None, make_log_event(namespace=" "), make_log_event(edge_site_id="other-site")])
async def test_lookup_requires_a_namespace_hint_from_the_selected_site(log_event):
    selected = selected_state(namespace="").root_cause_analysis.selected_action
    lookup = AsyncMock()

    with pytest.raises(ValueError, match="namespace is missing"):
        await resolve_target_with_lookup(selected, [], log_event, lookup)

    lookup.assert_not_called()


async def test_daemonset_namespace_uses_exact_cluster_lookup_without_alert_namespace():
    state = selected_state(site="edge-site-01", namespace="")
    update_selection(state, target={"kind": "DaemonSet", "name": "dns-default"})
    selected = state.root_cause_analysis.selected_action
    lookup = AsyncMock(
        return_value={
            "success": True,
            "matches": [
                {
                    "kind": "DaemonSet",
                    "name": "dns-default",
                    "namespace": "openshift-dns",
                    "edge_site_id": "edge-site-01",
                }
            ],
        }
    )

    resolved = await resolve_target_with_lookup(selected, [], None, lookup)

    assert resolved.target.namespace == "openshift-dns"
    assert selected.target.namespace == ""
    assert resolved.evidence == selected.evidence
    lookup.assert_awaited_once_with("get_daemonset", {"daemonset_name": "dns-default", "edge_site_id": "edge-site-01"})


@pytest.mark.parametrize(
    "matches",
    [
        None,
        [],
        [None],
        [{"kind": "DaemonSet", "name": "other", "namespace": "openshift-dns", "edge_site_id": "edge-site-01"}],
        [{"kind": "Deployment", "name": "dns-default", "namespace": "openshift-dns", "edge_site_id": "edge-site-01"}],
        [{"name": "dns-default", "namespace": "openshift-dns", "edge_site_id": "edge-site-01"}],
        [{"kind": "DaemonSet", "name": "dns-default", "edge_site_id": "edge-site-01"}],
        [{"kind": "DaemonSet", "name": "dns-default", "namespace": "openshift-dns", "edge_site_id": "other-site"}],
        [{"kind": "DaemonSet", "name": "dns-default", "namespace": " ", "edge_site_id": "edge-site-01"}],
        [
            {"kind": "DaemonSet", "name": "dns-default", "namespace": "openshift-dns", "edge_site_id": "edge-site-01"},
            {"kind": "DaemonSet", "name": "dns-default", "namespace": "kube-system", "edge_site_id": "edge-site-01"},
        ],
    ],
)
async def test_daemonset_namespace_rejects_missing_mismatched_or_ambiguous_lookup(matches):
    state = selected_state(site="edge-site-01", namespace="")
    update_selection(state, target={"kind": "DaemonSet", "name": "dns-default"})
    lookup = AsyncMock(return_value={"success": True, "matches": matches})

    with pytest.raises(ValueError, match="namespace lookup"):
        await resolve_target_with_lookup(state.root_cause_analysis.selected_action, [], None, lookup)

    lookup.assert_awaited_once()


async def test_daemonset_lookup_failure_is_not_accepted_as_namespace_evidence():
    state = selected_state(site="edge-site-01", namespace="")
    update_selection(state, target={"kind": "DaemonSet", "name": "dns-default"})
    lookup = AsyncMock(return_value={"success": False, "matches": []})

    with pytest.raises(ValueError, match="namespace lookup"):
        await resolve_target_with_lookup(state.root_cause_analysis.selected_action, [], None, lookup)
