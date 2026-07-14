import json

from loguru import logger

from agent_service.config import get_http_client


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


async def invoke_tool(tool_name: str, kwargs: dict) -> dict:
    """Call an MCP tool via LlamaStack's /v1/tool-runtime/invoke endpoint."""
    resp = await get_http_client().post(
        "/v1/tool-runtime/invoke",
        json={"tool_name": tool_name, "kwargs": kwargs},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error_message"):
        return {"success": False, "error": data["error_message"]}
    # Response content can be a JSON string or a list of typed content blocks
    content = data.get("content", "")
    try:
        if isinstance(content, str):
            return json.loads(content) if content else {}
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return json.loads(item["text"])
    except json.JSONDecodeError:
        return {"success": False, "error": f"unparseable response: {str(content)[:200]}"}
    return {}
