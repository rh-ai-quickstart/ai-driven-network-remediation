"""
LokiStack MCP Server
======================
MCP server for querying LokiStack via the LogQL API.
Gives the AI remediation agent access to historical log data.

Tools:
    query_logs        - Run a LogQL query against LokiStack
    get_recent_errors - Get recent error logs from a namespace/app
    count_errors      - Count error occurrences in a time window

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
