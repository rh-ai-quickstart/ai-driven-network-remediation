"""AAP MCP server configuration."""

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

MCP_TRANSPORT: Literal["stdio", "sse", "streamable-http"] = os.environ.get(
    "MCP_TRANSPORT", "sse"
)  # type: ignore[assignment]
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")

AAP_URL = os.getenv("AAP_URL", "https://aap.aap.svc")
AAP_API_PREFIX = os.getenv("AAP_API_PREFIX", "/api/controller/v2")
AAP_TOKEN = os.environ["AAP_TOKEN"]
AAP_VERIFY_SSL = os.getenv("AAP_VERIFY_SSL", "true").lower() == "true"
AAP_CA_BUNDLE = os.getenv("AAP_CA_BUNDLE", "")

GITEA_URL = os.getenv("GITEA_URL", "http://gitea.hub.svc:3000")
GITEA_OWNER = os.getenv("GITEA_OWNER", "noc")
GITEA_REPO = os.getenv("GITEA_REPO", "generated-playbooks")


GITEA_TOKEN_PATH = os.getenv(
    "GITEA_TOKEN_PATH", "/secrets/noc-gitea-secret/GITEA_TOKEN"
)


def get_gitea_token() -> str:
    try:
        return open(GITEA_TOKEN_PATH).read().strip()
    except FileNotFoundError:
        return os.getenv("GITEA_TOKEN", "")


mcp = FastMCP(
    "noc-aap",
    instructions=(
        "Ansible Automation Platform tools for triggering remediation playbooks. "
        "Use launch_job to execute Ansible playbooks on the edge cluster. "
        "Always check get_job_status after launching — don't assume success."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=(MCP_TRANSPORT == "streamable-http"),
)
