"""OpenShift tool implementations."""

import json
import subprocess

from .config import DEFAULT_NAMESPACE, EDGE_KUBECONFIG, mcp, resolve_kubeconfig

OC_TIMEOUT = 30


def _run_oc(
    args: list[str],
    kubeconfig: str = EDGE_KUBECONFIG,
    timeout: int = OC_TIMEOUT,
) -> dict:
    """Run an oc command and return parsed output."""
    cmd = ["oc", f"--kubeconfig={kubeconfig}"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Command timed out after {timeout}s", "returncode": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1, "success": False}


def _kubeconfig_for(edge_site_id: str = "") -> str:
    return resolve_kubeconfig(edge_site_id or None)


@mcp.tool()
def get_namespaces(edge_site_id: str = "") -> dict:
    """
    List all namespaces on the cluster with their status.

    Args:
        edge_site_id: Alert site label (edge-NN). Selects spoke kubeconfig in hub-spoke mode.

    Returns:
        Dict with namespaces list: [{name, status}]
    """
    result = _run_oc(["get", "namespaces", "-o", "json"], kubeconfig=_kubeconfig_for(edge_site_id))

    if not result["success"]:
        return {"error": result["stderr"], "namespaces": []}

    try:
        data = json.loads(result["stdout"])
        namespaces = []
        for ns in data.get("items", []):
            namespaces.append(
                {
                    "name": ns["metadata"]["name"],
                    "status": ns["status"].get("phase", "Unknown"),
                }
            )
        return {"namespaces": namespaces, "count": len(namespaces)}
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse namespace output: {e}", "namespaces": []}


@mcp.tool()
def get_pods(namespace: str = DEFAULT_NAMESPACE, edge_site_id: str = "") -> dict:
    """
    List all pods in the specified namespace with their status.

    Args:
        namespace: OpenShift namespace to query (default: dark-noc-edge)
        edge_site_id: Alert site label (edge-NN). Selects spoke kubeconfig in hub-spoke mode.

    Returns:
        Dict with pods list: [{name, status, restart_count, node, ready}]
    """
    result = _run_oc(
        ["get", "pods", "-n", namespace, "-o", "json"],
        kubeconfig=_kubeconfig_for(edge_site_id),
    )

    if not result["success"]:
        return {"error": result["stderr"], "pods": []}

    try:
        data = json.loads(result["stdout"])
        pods = []
        for pod in data.get("items", []):
            name = pod["metadata"]["name"]
            phase = pod["status"].get("phase", "Unknown")
            containers = pod["status"].get("containerStatuses", [])
            restarts = sum(c.get("restartCount", 0) for c in containers)
            node = pod["spec"].get("nodeName", "unknown")
            pods.append(
                {
                    "name": name,
                    "status": phase,
                    "restart_count": restarts,
                    "node": node,
                    "ready": all(c.get("ready", False) for c in containers),
                }
            )
        return {"namespace": namespace, "pods": pods, "count": len(pods)}
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse pod output: {e}", "pods": []}


@mcp.tool()
def get_events(
    namespace: str = DEFAULT_NAMESPACE,
    limit: int = 20,
    edge_site_id: str = "",
) -> dict:
    """
    Get recent OpenShift events (especially warnings) from a namespace.

    Args:
        namespace: OpenShift namespace (default: dark-noc-edge)
        limit:     Maximum number of events to return (default: 20)
        edge_site_id: Alert site label (edge-NN). Selects spoke kubeconfig in hub-spoke mode.

    Returns:
        Dict with events list: [{type, reason, message, object, time, count}]
    """
    result = _run_oc(
        ["get", "events", "-n", namespace, "--sort-by=lastTimestamp", "-o", "json"],
        kubeconfig=_kubeconfig_for(edge_site_id),
    )

    if not result["success"]:
        return {"error": result["stderr"], "events": []}

    try:
        data = json.loads(result["stdout"])
        events = []
        for evt in data.get("items", [])[-limit:]:
            events.append(
                {
                    "type": evt.get("type", "Normal"),
                    "reason": evt.get("reason", ""),
                    "message": evt.get("message", ""),
                    "object": f"{evt['involvedObject']['kind']}/{evt['involvedObject']['name']}",
                    "time": evt.get("lastTimestamp") or evt.get("eventTime") or "",
                    "count": evt.get("count", 1),
                }
            )
        events.sort(key=lambda e: (0 if e["type"] == "Warning" else 1, e["time"] or ""))
        return {"namespace": namespace, "events": events}
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse events: {e}", "events": []}


@mcp.tool()
def rollout_restart(
    deployment: str,
    namespace: str = DEFAULT_NAMESPACE,
    edge_site_id: str = "",
) -> dict:
    """
    Trigger a rolling restart of a deployment (safe — no downtime if replicas > 1).

    Args:
        deployment: Name of the Deployment to restart
        namespace:  Namespace of the deployment (default: dark-noc-edge)
        edge_site_id: Alert site label (edge-NN). Selects spoke kubeconfig in hub-spoke mode.

    Returns:
        Dict with restart status and message
    """
    kc = _kubeconfig_for(edge_site_id)
    result = _run_oc(
        ["rollout", "restart", f"deployment/{deployment}", "-n", namespace],
        kubeconfig=kc,
    )

    if not result["success"]:
        return {"success": False, "error": result["stderr"]}

    wait_result = _run_oc(
        [
            "rollout",
            "status",
            f"deployment/{deployment}",
            "-n",
            namespace,
            "--timeout=90s",
        ],
        kubeconfig=kc,
        timeout=120,
    )

    return {
        "success": wait_result["success"],
        "deployment": deployment,
        "namespace": namespace,
        "message": wait_result["stdout"].strip() or wait_result["stderr"].strip(),
    }


@mcp.tool()
def patch_deployment_memory(
    deployment: str,
    memory_limit: str,
    namespace: str = DEFAULT_NAMESPACE,
    edge_site_id: str = "",
) -> dict:
    """
    Patch a deployment's memory limit (useful for OOMKilled remediation).

    Args:
        deployment:   Deployment name
        memory_limit: New memory limit (e.g., "512Mi", "1Gi")
        namespace:    Namespace (default: dark-noc-edge)
        edge_site_id: Alert site label (edge-NN). Selects spoke kubeconfig in hub-spoke mode.

    Returns:
        Dict with patch status
    """
    patch = json.dumps(
        [
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/resources/limits/memory",
                "value": memory_limit,
            }
        ]
    )

    result = _run_oc(
        [
            "patch",
            "deployment",
            deployment,
            "-n",
            namespace,
            "--type=json",
            f"-p={patch}",
        ],
        kubeconfig=_kubeconfig_for(edge_site_id),
    )

    return {
        "success": result["success"],
        "deployment": deployment,
        "new_memory_limit": memory_limit,
        "message": result["stdout"] or result["stderr"],
    }


@mcp.tool()
def get_pod_spec(
    name: str,
    namespace: str,
    edge_site_id: str = "",
) -> dict:
    """
    Get the structured spec for a pod as JSON. Includes container resource
    limits, requests, probes, env vars, and other spec fields.

    If an exact pod name match is not found, falls back to prefix matching
    (e.g. "myapp" matches "myapp-6b7f8c9d4-x2k9z").

    Args:
        name:      Pod name (exact or prefix)
        namespace: Namespace
        edge_site_id: Alert site label (edge-NN). Selects spoke kubeconfig in hub-spoke mode.

    Returns:
        Dict with parsed spec and success flag.
    """
    kc = _kubeconfig_for(edge_site_id)
    result = _run_oc(["get", "pod", name, "-n", namespace, "-o", "json"], kubeconfig=kc)
    if result["success"]:
        return _parse_pod_spec(name, namespace, result["stdout"])

    if _is_not_found(result):
        pod, list_error = _find_pod(name, namespace, kc)
        if pod is not None:
            return _pod_spec_ok(pod.get("metadata", {}).get("name", name), namespace, pod.get("spec", {}))
        if list_error is not None:
            return _pod_spec_error(name, namespace, list_error)
        return _pod_spec_error(name, namespace, f"no pod matching '{name}'")
    return _pod_spec_error(name, namespace, result["stderr"])


def _is_not_found(result: dict) -> bool:
    """True when an oc command failed because the resource does not exist.

    Real oc output is ``Error from server (NotFound): pods "x" not found``;
    match case-insensitively so RBAC (Forbidden) and container errors do not
    trigger the prefix fallback.
    """
    return not result["success"] and "not found" in result["stderr"].lower()


def _find_pod(name: str, namespace: str, kubeconfig: str) -> tuple[dict | None, str | None]:
    """Resolve name to a live pod, returning (pod, error).

    Lists pods once so callers can resolve a deployment/partial name to a live
    pod (e.g. "myapp" matches "myapp-6b7f8c9d4-x2k9z"). A pod matches when its
    name equals name or starts with ``name + "-"`` so "app" cannot match
    "application-...".

    Returns (pod, None) on a match, (None, error) when the list call fails or
    its output is unparseable, and (None, None) when the list succeeded but
    nothing matched.
    """
    list_result = _run_oc(["get", "pods", "-n", namespace, "-o", "json"], kubeconfig=kubeconfig)
    if not list_result["success"]:
        return None, list_result["stderr"]
    try:
        pods = json.loads(list_result["stdout"])
    except json.JSONDecodeError as exc:
        return None, f"failed to parse pod list: {exc}"
    for pod in pods.get("items", []):
        pod_name = pod.get("metadata", {}).get("name", "")
        if pod_name == name or pod_name.startswith(name + "-"):
            return pod, None
    return None, None


def _parse_pod_spec(name: str, namespace: str, stdout: str) -> dict:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return _pod_spec_error(name, namespace, f"JSON parse error: {exc}")
    return _pod_spec_ok(name, namespace, parsed.get("spec", parsed))


def _pod_spec_ok(name: str, namespace: str, spec: dict) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "spec": spec,
        "success": True,
        "error": None,
    }


def _pod_spec_error(name: str, namespace: str, error: str) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "spec": {},
        "success": False,
        "error": error,
    }


@mcp.tool()
def get_pod_logs(
    pod_name: str,
    namespace: str = DEFAULT_NAMESPACE,
    container: str = "",
    tail_lines: int = 50,
    edge_site_id: str = "",
) -> dict:
    """
    Get recent logs from a specific pod.

    If an exact pod name is not found, falls back to prefix matching
    (e.g. "myapp" matches "myapp-6b7f8c9d4-x2k9z").

    Args:
        pod_name:   Pod name (exact or prefix)
        namespace:  Namespace (default: dark-noc-edge)
        container:  Container name (optional, for multi-container pods)
        tail_lines: Number of log lines to return (default: 50)
        edge_site_id: Alert site label (edge-NN). Selects spoke kubeconfig in hub-spoke mode.

    Returns:
        Dict with logs string
    """
    kc = _kubeconfig_for(edge_site_id)

    def _logs_args(target: str) -> list[str]:
        args = ["logs", target, "-n", namespace, f"--tail={tail_lines}"]
        if container:
            args += ["-c", container]
        return args

    result = _run_oc(_logs_args(pod_name), kubeconfig=kc)
    if _is_not_found(result):
        pod, _ = _find_pod(pod_name, namespace, kc)
        matched = pod.get("metadata", {}).get("name", "") if pod else ""
        if matched and matched != pod_name:
            pod_name = matched
            result = _run_oc(_logs_args(pod_name), kubeconfig=kc)

    return {
        "pod": pod_name,
        "namespace": namespace,
        "logs": result["stdout"],
        "success": result["success"],
        "error": result["stderr"] if not result["success"] else None,
    }
