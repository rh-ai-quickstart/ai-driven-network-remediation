import json
import re

from loguru import logger

from agent_service.config import get_http_client

# Prefix stamped onto pod-spec evidence in investigate.py to record which cluster
# it came from. Retargeting only trusts an edge_site_id that carries this stamp,
# so a namespace or log line that merely mentions a cluster name is not enough.
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


def _normalize_component_name(component: str) -> str:
    """Turn a free-text affected component into a matchable pod-name prefix.

    Drops any '(...)' qualifier, a leading 'kind/' segment, and trailing words so
    'deployment/orders-api (edge-site-02)' resolves to 'orders-api'.
    """
    without_paren = re.sub(r"\(.*?\)", " ", component)
    last_segment = without_paren.strip().split("/")[-1]
    tokens = last_segment.split()
    return tokens[0].lower() if tokens else ""


def build_launch_extra_vars(log_event, llm_summary=None, evidence_text="") -> dict:
    """Build the extra_vars dict from a log event for AAP job launches."""
    if not log_event:
        return {}
    extra_vars = {
        "namespace": log_event.namespace,
        "pod_name": log_event.pod_name,
        "container": log_event.container,
        "edge_site_id": log_event.edge_site_id,
    }
    if not llm_summary:
        return extra_vars
    component = llm_summary.get("affected_component")
    if isinstance(component, str) and component and re.search(rf"\b{re.escape(component)}\b", evidence_text):
        extra_vars["deployment_name"] = component
    site_id = llm_summary.get("edge_site_id")
    if isinstance(site_id, str) and site_id and f"{EDGE_SITE_STAMP} {site_id}" in evidence_text:
        extra_vars["edge_site_id"] = site_id
    return extra_vars


def _parse_tool_content(content) -> dict:
    """Parse an MCP response body: a JSON string or a list of typed content blocks."""
    if isinstance(content, str):
        return json.loads(content) if content else {}
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return json.loads(block["text"])
    return {}


async def invoke_tool(tool_name: str, kwargs: dict) -> dict:
    """Call an MCP tool via LlamaStack's /v1/tool-runtime/invoke endpoint."""
    logger.info(f"MCP tool invoke: {tool_name} args={kwargs}")
    resp = await get_http_client().post(
        "/v1/tool-runtime/invoke",
        json={"tool_name": tool_name, "kwargs": kwargs},
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
