"""
OpenShift API MCP Server
=========================
MCP server exposing OpenShift operations as tools
for the AI-driven network remediation agent.

Tools:
    get_namespaces         - List all namespaces on the cluster
    get_pods               - List pods in a namespace with status
    get_events             - Get recent OpenShift events (warnings)
    rollout_restart        - Trigger a rolling restart
    patch_deployment_memory - Patch deployment memory limits
    get_pod_logs           - Get recent logs from a specific pod

Transport: Configurable via MCP_TRANSPORT env var (default: sse)
"""

import asyncio
from typing import Any

from starlette.responses import JSONResponse

from .config import MCP_TRANSPORT, mcp


@mcp.custom_route("/health", methods=["GET"])  # type: ignore
async def health(request: Any) -> JSONResponse:
    """Health check endpoint for Kubernetes probes."""
    return JSONResponse({"status": "OK"})


@mcp.custom_route("/demo/reset", methods=["POST"])
async def reset_demo(request: Any) -> JSONResponse:
    from .tools import reset_cpu_demo

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"message": "Invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"message": "Expected a JSON object"}, status_code=400)
    namespace = payload.get("namespace")
    site = payload.get("edge_site_id", "")
    if not isinstance(namespace, str) or not namespace or not isinstance(site, str):
        return JSONResponse({"message": "namespace and edge_site_id must be strings"}, status_code=400)
    reset_status = await asyncio.to_thread(reset_cpu_demo, namespace, site)
    return JSONResponse(reset_status, status_code=200 if reset_status["success"] else 502)


def main() -> None:
    """Run the OpenShift MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


from . import tools as _tools

app = mcp.streamable_http_app()
