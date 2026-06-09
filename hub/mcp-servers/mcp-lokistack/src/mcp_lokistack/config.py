"""LokiStack MCP server configuration."""

import os
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

MCP_TRANSPORT: Literal["stdio", "sse", "streamable-http"] = os.environ.get(
    "MCP_TRANSPORT", "sse"
)  # type: ignore[assignment]
MCP_PORT = int(os.environ.get("MCP_PORT", "8002"))
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")

LOKI_URL = os.getenv(
    "LOKI_URL",
    "https://logging-loki-gateway-http.openshift-logging.svc:8080",
)
LOKI_TOKEN = os.getenv("LOKI_TOKEN", "")
LOKI_TOKEN_PATH = os.getenv("LOKI_TOKEN_PATH", "")
LOKI_TLS_VERIFY = os.getenv("LOKI_TLS_VERIFY", "false").lower() == "true"
LOKI_CA_CERT_PATH = os.getenv("LOKI_CA_CERT_PATH", "")

LOKI_DEFAULT_TENANT = os.getenv("LOKI_DEFAULT_TENANT", "application")
VALID_TENANTS = ("application", "infrastructure", "audit")

LOKI_MAX_LINES = int(os.getenv("LOKI_MAX_LINES", "100"))  # cap log lines to avoid blowing up the agent's context window
LOKI_MAX_LINES_CEILING = int(os.getenv("LOKI_MAX_LINES_CEILING", "500"))
LOKI_MAX_DURATION = os.getenv("LOKI_MAX_DURATION", "24h")
LOKI_QUERY_TIMEOUT = int(os.getenv("LOKI_QUERY_TIMEOUT", "30"))
LOKI_RETRY_ATTEMPTS = int(os.getenv("LOKI_RETRY_ATTEMPTS", "3"))

DEFAULT_SEVERITY_REGEX = r"(?i)(error|fatal|critical|panic|exception)"


def read_token() -> str:
    if LOKI_TOKEN_PATH:
        try:
            return Path(LOKI_TOKEN_PATH).read_text().strip()
        except OSError:
            pass
    return LOKI_TOKEN


mcp = FastMCP(
    "noc-lokistack",
    instructions=(
        "LokiStack log query tools for the NOC remediation agent. "
        "Requires OpenShift with the LokiStack Operator "
        "(openshift-logging namespace).\n"
        "Tenants: application (workload logs, default), "
        "infrastructure (node/system), audit (API audit).\n"
        "Prefer search_logs/search_logs_regex over query_logql "
        "— they build correct LogQL for you.\n"
        "Time ranges: '30m', '1h', '6h', '24h'."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=(MCP_TRANSPORT == "streamable-http"),
)
