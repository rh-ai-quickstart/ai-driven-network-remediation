"""
Slack MCP Server
=================
MCP server wrapping the Slack Bot API as tools for the AI remediation agent.
Sends NOC alerts, remediation summaries, and status updates to Slack.

Tools:
    send_alert          - Send a formatted NOC alert with severity color
    send_message        - Send a plain text message
    send_remediation    - Send a remediation summary with status
    send_incident_ticket - Send ServiceNow ticket info to Slack

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
    """Run the Slack MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


from . import tools as _tools

app = mcp.streamable_http_app()
