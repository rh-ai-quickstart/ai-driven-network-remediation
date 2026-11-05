from collections.abc import Awaitable, Callable

from agent_service.models import LogEvent, SelectedAction


def resolve_selected_target(action: SelectedAction, deployment_observations: list[dict]) -> SelectedAction:
    """Complete omitted namespaces from observed identities, never from the alert's target."""
    target = action.target
    if target.cluster_scoped:
        if target.namespace.strip() or target.kind.casefold() in {"deployment", "daemonset"}:
            raise ValueError("Selected target has conflicting namespace and cluster-scoped identity")
        return action
    if target.namespace.strip():
        return action
    namespaces = {
        observation["namespace"]
        for observation in deployment_observations
        if target.kind.casefold() == "deployment"
        and observation.get("edge_site_id") == target.edge_site_id
        and observation.get("deployment") == target.name
        and isinstance(observation.get("namespace"), str)
        and observation["namespace"].strip()
    }
    if len(namespaces) != 1:
        reason = "ambiguous" if namespaces else "missing"
        raise ValueError(f"Selected {target.kind} {target.name} namespace is {reason}; resolve it from target evidence")
    resolved_target = target.model_copy(update={"namespace": namespaces.pop()})
    return action.model_copy(update={"target": resolved_target})


async def resolve_target_with_lookup(
    action: SelectedAction,
    deployment_observations: list[dict],
    log_event: LogEvent | None,
    lookup: Callable[[str, dict], Awaitable[dict]],
) -> SelectedAction:
    """Use fresh target evidence to complete omitted namespaces."""
    try:
        return resolve_selected_target(action, deployment_observations)
    except ValueError:
        if _can_lookup_daemonset(action):
            return await _resolve_daemonset_target(action, lookup)
        if not _can_lookup_deployment(action, deployment_observations, log_event):
            raise
    target = action.target
    identity = {
        "deployment": target.name,
        "namespace": log_event.namespace.strip(),
        "edge_site_id": target.edge_site_id,
    }
    observation = await lookup("get_deployment", identity)
    if observation.get("success") is not True or any(
        observation.get(key) != identity[key] for key in ("deployment", "namespace")
    ):
        raise ValueError("Selected Deployment namespace lookup did not confirm the exact target identity")
    if observation.get("edge_site_id", target.edge_site_id) != target.edge_site_id:
        raise ValueError("Selected Deployment namespace lookup returned a different edge site")
    return resolve_selected_target(action, [identity])


def _can_lookup_daemonset(action: SelectedAction) -> bool:
    target = action.target
    return not target.cluster_scoped and not target.namespace.strip() and target.kind.casefold() == "daemonset"


async def _resolve_daemonset_target(
    action: SelectedAction,
    lookup: Callable[[str, dict], Awaitable[dict]],
) -> SelectedAction:
    target = action.target
    lookup_response = await lookup(
        "get_daemonset", {"daemonset_name": target.name, "edge_site_id": target.edge_site_id}
    )
    target_matches = lookup_response.get("matches")
    if lookup_response.get("success") is not True:
        raise ValueError("Selected DaemonSet namespace lookup failed to confirm the exact target identity")
    if not isinstance(target_matches, list) or not target_matches:
        raise ValueError(f"Selected DaemonSet {target.name} namespace lookup found no exact target")
    if len(target_matches) > 1:
        raise ValueError(f"Selected DaemonSet {target.name} namespace lookup is ambiguous")
    target_match = target_matches[0]
    if not isinstance(target_match, dict):
        raise ValueError("Selected DaemonSet namespace lookup did not confirm the exact target identity")
    namespace = target_match.get("namespace")
    kind = target_match.get("kind")
    if (
        not isinstance(kind, str)
        or kind.casefold() != "daemonset"
        or target_match.get("name") != target.name
        or target_match.get("edge_site_id") != target.edge_site_id
        or not isinstance(namespace, str)
        or not namespace.strip()
        or namespace != namespace.strip()
    ):
        raise ValueError("Selected DaemonSet namespace lookup did not confirm the exact target identity")
    resolved_target = target.model_copy(update={"namespace": namespace})
    return action.model_copy(update={"target": resolved_target})


def _can_lookup_deployment(action: SelectedAction, observations: list[dict], log_event: LogEvent | None) -> bool:
    target = action.target
    if target.cluster_scoped or target.namespace or target.kind.casefold() != "deployment":
        return False
    if not log_event or not log_event.namespace.strip() or log_event.edge_site_id != target.edge_site_id:
        return False
    return not any(
        observation.get("deployment") == target.name and observation.get("edge_site_id") == target.edge_site_id
        for observation in observations
    )
