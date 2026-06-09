"""
LokiStack MCP Server
======================
MCP server for querying LokiStack via the LogQL API on OpenShift.
Requires an OpenShift cluster with the LokiStack Operator deployed
in the openshift-logging namespace.

Tools:
    search_logs          - Search by namespace/pod/container/text or raw LogQL
    query_metrics        - Error rate, log volume, or top errors by count
    find_error_patterns  - Group recurring errors by normalized message

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
    """Run the LokiStack MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


from . import tools as _tools

app = mcp.streamable_http_app()
