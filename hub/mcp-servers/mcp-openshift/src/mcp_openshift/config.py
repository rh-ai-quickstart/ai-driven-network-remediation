"""OpenShift MCP server configuration."""

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

MCP_TRANSPORT: Literal["stdio", "sse", "streamable-http"] = os.environ.get(
    "MCP_TRANSPORT", "sse"
)  # type: ignore[assignment]
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")

EDGE_KUBECONFIG = os.getenv("EDGE_KUBECONFIG", "/kubeconfig/kubeconfig")
DEFAULT_NAMESPACE = os.getenv("DEFAULT_NAMESPACE", "dark-noc-edge")

mcp = FastMCP(
    "noc-openshift",
    instructions=(
        "OpenShift cluster management tools for the NOC remediation agent. "
        "Use these tools to inspect pod status, get logs, patch deployments, "
        "and trigger restarts on the edge cluster."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=(MCP_TRANSPORT == "streamable-http"),
)
