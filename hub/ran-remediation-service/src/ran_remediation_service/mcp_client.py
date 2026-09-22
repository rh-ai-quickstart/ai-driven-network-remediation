"""MCP tool invocation via LlamaStack /v1/tool-runtime/invoke.

Independent reimplementation of the same pattern used by agent-service —
no cross-service imports, same interface.
"""

from __future__ import annotations

import json

from loguru import logger

from ran_remediation_service.config import get_http_client


def _parse_tool_content(content) -> dict:
    """Parse MCP response body: a JSON string or a list of typed content blocks."""
    if isinstance(content, str):
        return json.loads(content) if content else {}
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return json.loads(block["text"])
    return {}


async def invoke_tool(tool_name: str, kwargs: dict) -> dict:
    """Call an MCP tool via LlamaStack's /v1/tool-runtime/invoke endpoint."""
    logger.info("MCP tool invoke: {} args={}", tool_name, kwargs)
    resp = await get_http_client().post(
        "/v1/tool-runtime/invoke",
        json={"tool_name": tool_name, "kwargs": kwargs},
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error_message"):
        logger.warning("MCP tool {} returned error: {}", tool_name, data["error_message"])
        return {"success": False, "error": data["error_message"]}

    content = data.get("content", "")
    try:
        parsed = _parse_tool_content(content)
    except json.JSONDecodeError:
        preview = str(content)[:200]
        logger.warning("MCP tool {} unparseable response: {}", tool_name, preview)
        return {"success": False, "error": f"unparseable response: {preview}"}

    logger.debug("MCP tool {} succeeded", tool_name)
    return parsed
