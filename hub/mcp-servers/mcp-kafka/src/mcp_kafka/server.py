"""
Kafka MCP Server
=================
MCP server for Kafka operations.
Allows the AI remediation agent to read from topics and produce messages.

Tools:
    consume_topic   - Read recent messages from a topic
    produce_message - Write a message to a topic
    get_consumer_lag - Check consumer group lag
    list_topics     - List all available topics

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
    """Run the Kafka MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


from . import tools as _tools

app = mcp.streamable_http_app()
