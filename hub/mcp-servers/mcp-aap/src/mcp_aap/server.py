"""
Ansible Automation Platform MCP Server
========================================
MCP server wrapping the AAP 2.5 REST API as tools
for the AI-driven network remediation agent.

Tools:
    list_job_templates  - List available Ansible job templates
    launch_job          - Trigger a job template execution
    upsert_job_template - Create/update a template for a playbook path
    get_job_status      - Poll job completion status
    get_job_output      - Get stdout from a completed/failed job

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
    """Run the AAP MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


from . import tools as _tools

app = mcp.streamable_http_app()
