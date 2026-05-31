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

from typing import Any

from starlette.responses import JSONResponse

from .config import MCP_TRANSPORT, mcp


@mcp.custom_route("/health", methods=["GET"])  # type: ignore
async def health(request: Any) -> JSONResponse:
    """Health check endpoint for Kubernetes probes."""
    return JSONResponse({"status": "OK"})


def main() -> None:
    """Run the OpenShift MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


from . import tools as _tools  # noqa: E402, F401

app = mcp.streamable_http_app()
