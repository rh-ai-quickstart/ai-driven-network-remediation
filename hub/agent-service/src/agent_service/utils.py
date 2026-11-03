import json
import re

from loguru import logger

from agent_service.config import EDGE_NAMESPACE, get_http_client

# Prefix stamped onto investigation evidence to record its source cluster.
EDGE_SITE_STAMP = "Edge site:"


async def warm_tool_cache() -> bool:
    """Call /v1/tools so LlamaStack indexes MCP tools into its routing cache."""
    try:
        resp = await get_http_client().get("/v1/tools")
        resp.raise_for_status()
    except Exception:
        logger.opt(exception=True).warning("Failed to warm LlamaStack tool cache")
        return False
    tools = resp.json().get("data") or []
    logger.info(f"LlamaStack tool cache warmed: {len(tools)} tools indexed")
    return True


# A Deployment pod name is "<deployment>-<replicaset-hash>-<pod-hash>". Match the
# trailing two hash segments so a bare workload name (e.g. "memory-hog") is left
# intact instead of collapsing to "" the way a blind two-segment strip would.
_POD_HASH_SUFFIX = re.compile(r"-[a-z0-9]{5,10}-[a-z0-9]{5}$")


def derive_deployment_name(pod_name: str) -> str:
    """Strip the ReplicaSet + pod hash suffix from a Deployment pod name."""
    return _POD_HASH_SUFFIX.sub("", pod_name) if pod_name else pod_name


_EDGE_NGINX_DEPLOYMENT = "edge-nginx"


def resolve_remediation_deployment(namespace: str, pod_name: str) -> str:
    """Deployment name for restart-nginx and similar playbooks (``deployment`` extra var)."""
    pod = pod_name or ""
    ns = namespace or ""
    if not pod:
        return ""
    derived = derive_deployment_name(pod) or pod
    if derived == _EDGE_NGINX_DEPLOYMENT:
        return _EDGE_NGINX_DEPLOYMENT
    # Standalone nginx-edge-* pods (synthetic alerts) and edge-nginx-* ReplicaSet pods
    # both target the edge-nginx Deployment on the spoke.
    if ns == EDGE_NAMESPACE and (pod.startswith("nginx-edge-") or pod.startswith("edge-nginx-")):
        return _EDGE_NGINX_DEPLOYMENT
    return derived


def build_launch_extra_vars(log_event) -> dict:
    """Build the extra_vars dict from a log event for AAP job launches."""
    if not log_event:
        return {}
    deployment = resolve_remediation_deployment(
        log_event.namespace or "",
        log_event.pod_name or "",
    )
    return {
        "namespace": log_event.namespace,
        "pod_name": log_event.pod_name,
        "container": log_event.container,
        "edge_site_id": log_event.edge_site_id,
        "deployment": deployment,
        "deployment_name": deployment,
    }


def _parse_tool_content(content) -> dict:
    """Parse an MCP response body: a JSON string or a list of typed content blocks."""
    if isinstance(content, str):
        return json.loads(content) if content else {}
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return json.loads(block["text"])
    return {}


async def invoke_tool(tool_name: str, kwargs: dict, *, timeout_seconds: float | None = None) -> dict:
    """Call an MCP tool via LlamaStack's /v1/tool-runtime/invoke endpoint."""
    logger.info(f"MCP tool invoke: {tool_name} args={kwargs}")
    request_options = {"timeout": timeout_seconds} if timeout_seconds is not None else {}
    resp = await get_http_client().post(
        "/v1/tool-runtime/invoke",
        json={"tool_name": tool_name, "kwargs": kwargs},
        **request_options,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error_message"):
        logger.warning(f"MCP tool {tool_name} returned error: {data['error_message']}")
        return {"success": False, "error": data["error_message"]}

    content = data.get("content", "")
    try:
        parsed = _parse_tool_content(content)
    except json.JSONDecodeError:
        preview = str(content)[:200]
        logger.warning(f"MCP tool {tool_name} unparseable response: {preview}")
        return {"success": False, "error": f"unparseable response: {preview}"}

    logger.debug(f"MCP tool {tool_name} succeeded, response={parsed}")
    return parsed
