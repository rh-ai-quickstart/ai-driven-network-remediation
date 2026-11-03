from collections.abc import Awaitable, Callable

from agent_service.models import LogEvent, SelectedAction


def resolve_selected_target(action: SelectedAction, deployment_observations: list[dict]) -> SelectedAction:
    """Complete omitted namespaces from observed identities, never from the alert's target."""
    target = action.target
    if target.cluster_scoped:
        if target.namespace.strip() or target.kind.casefold() == "deployment":
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
    """Use an alert namespace only as a lookup hint, requiring fresh target confirmation."""
    try:
        return resolve_selected_target(action, deployment_observations)
    except ValueError:
        if not _can_lookup_target(action, deployment_observations, log_event):
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


def _can_lookup_target(action: SelectedAction, observations: list[dict], log_event: LogEvent | None) -> bool:
    target = action.target
    if target.cluster_scoped or target.namespace or target.kind.casefold() != "deployment":
        return False
    if not log_event or not log_event.namespace.strip() or log_event.edge_site_id != target.edge_site_id:
        return False
    return not any(
        observation.get("deployment") == target.name and observation.get("edge_site_id") == target.edge_site_id
        for observation in observations
    )
