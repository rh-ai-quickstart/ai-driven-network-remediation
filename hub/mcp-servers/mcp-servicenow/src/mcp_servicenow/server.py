"""
ServiceNow MCP Server
======================
MCP server wrapping the ServiceNow REST API for incident management.
In production, point SERVICENOW_URL to a real ServiceNow instance.

Tools:
    create_incident  - Open a new ServiceNow incident ticket
    update_incident  - Add work notes / update status
    get_incident     - Get incident details by number
    resolve_incident - Close an incident with resolution notes

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
    """Run the ServiceNow MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


from . import tools as _tools

app = mcp.streamable_http_app()
