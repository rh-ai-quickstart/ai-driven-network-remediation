"""OpenShift tool implementations."""

import json
import subprocess

from .config import DEFAULT_NAMESPACE, EDGE_KUBECONFIG, mcp

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


@mcp.tool()
def get_namespaces() -> dict:
    """
    List all namespaces on the cluster with their status.

    Returns:
        Dict with namespaces list: [{name, status}]
    """
    result = _run_oc(["get", "namespaces", "-o", "json"])

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
def get_pods(namespace: str = DEFAULT_NAMESPACE) -> dict:
    """
    List all pods in the specified namespace with their status.

    Args:
        namespace: OpenShift namespace to query (default: dark-noc-edge)

    Returns:
        Dict with pods list: [{name, status, restart_count, node, ready}]
    """
    result = _run_oc(["get", "pods", "-n", namespace, "-o", "json"])

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
def get_events(namespace: str = DEFAULT_NAMESPACE, limit: int = 20) -> dict:
    """
    Get recent OpenShift events (especially warnings) from a namespace.

    Args:
        namespace: OpenShift namespace (default: dark-noc-edge)
        limit:     Maximum number of events to return (default: 20)

    Returns:
        Dict with events list: [{type, reason, message, object, time, count}]
    """
    result = _run_oc(["get", "events", "-n", namespace, "--sort-by=lastTimestamp", "-o", "json"])

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
def rollout_restart(deployment: str, namespace: str = DEFAULT_NAMESPACE) -> dict:
    """
    Trigger a rolling restart of a deployment (safe — no downtime if replicas > 1).

    Args:
        deployment: Name of the Deployment to restart
        namespace:  Namespace of the deployment (default: dark-noc-edge)

    Returns:
        Dict with restart status and message
    """
    result = _run_oc(["rollout", "restart", f"deployment/{deployment}", "-n", namespace])

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
) -> dict:
    """
    Patch a deployment's memory limit (useful for OOMKilled remediation).

    Args:
        deployment:   Deployment name
        memory_limit: New memory limit (e.g., "512Mi", "1Gi")
        namespace:    Namespace (default: dark-noc-edge)

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
        ]
    )

    return {
        "success": result["success"],
        "deployment": deployment,
        "new_memory_limit": memory_limit,
        "message": result["stdout"] or result["stderr"],
    }


@mcp.tool()
def get_pod_logs(
    pod_name: str,
    namespace: str = DEFAULT_NAMESPACE,
    container: str = "",
    tail_lines: int = 50,
) -> dict:
    """
    Get recent logs from a specific pod.

    Args:
        pod_name:   Pod name
        namespace:  Namespace (default: dark-noc-edge)
        container:  Container name (optional, for multi-container pods)
        tail_lines: Number of log lines to return (default: 50)

    Returns:
        Dict with logs string
    """
    args = ["logs", pod_name, "-n", namespace, f"--tail={tail_lines}"]
    if container:
        args += ["-c", container]

    result = _run_oc(args)
    return {
        "pod": pod_name,
        "namespace": namespace,
        "logs": result["stdout"],
        "success": result["success"],
        "error": result["stderr"] if not result["success"] else None,
    }
