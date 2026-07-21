"""OpenShift MCP server configuration."""

import os
import re
from typing import Literal

from mcp.server.fastmcp import FastMCP

MCP_TRANSPORT: Literal["stdio", "sse", "streamable-http"] = os.environ.get(
    "MCP_TRANSPORT", "sse"
)  # type: ignore[assignment]
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")

# Single-cluster fallback (edge-rbac Job or Kind --set-file secret).
EDGE_KUBECONFIG = os.getenv("EDGE_KUBECONFIG", "/kubeconfig/kubeconfig")
# Hub-spoke: one kubeconfig per ManagedCluster under this directory.
KUBECONFIG_DIR = os.getenv("KUBECONFIG_DIR", "/kubeconfigs")
SPOKE_NAME_PREFIX = os.getenv("SPOKE_NAME_PREFIX", "edge-site")
# Set to hub-spoke by topology overlay; avoids falling back to a missing single-cluster mount.
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "single-cluster")
DEFAULT_NAMESPACE = os.getenv("DEFAULT_NAMESPACE", "dark-noc-edge")

_EDGE_SITE_LABEL = re.compile(r"^edge-(\d+)$")
_SPOKE_NAME = re.compile(r"^(.+)-(\d+)$")


def site_id_to_spoke_name(
    site_id: str,
    *,
    prefix: str | None = None,
) -> str:
    """Map alert label edge_site_id to ManagedCluster / secret suffix name.

    edge-01 → edge-site-01 (default prefix; zero-padded)
    edge-1 → edge-site-01 (normalized)
    edge-site-01 → edge-site-01 (already a spoke name)
    """
    use_prefix = SPOKE_NAME_PREFIX if prefix is None else prefix
    sid = (site_id or "").strip()
    if not sid or sid == "unknown":
        return ""
    if sid.startswith(f"{use_prefix}-"):
        match = _SPOKE_NAME.fullmatch(sid)
        if match and match.group(1) == use_prefix:
            return f"{use_prefix}-{int(match.group(2)):02d}"
        return sid
    match = _EDGE_SITE_LABEL.fullmatch(sid)
    if match:
        return f"{use_prefix}-{int(match.group(1)):02d}"
    return sid


def resolve_kubeconfig(edge_site_id: str | None = None) -> str:
    """Resolve oc --kubeconfig path for an edge site.

    Hub-spoke: always /kubeconfigs/<edge-site-NN>/kubeconfig (no single-cluster fallback).
    Single-cluster: per-spoke mount when present, else EDGE_KUBECONFIG.
    """
    spoke = site_id_to_spoke_name(edge_site_id or "")
    if DEPLOYMENT_MODE == "hub-spoke":
        if not spoke:
            return os.path.join(KUBECONFIG_DIR, "unspecified-edge-site", "kubeconfig")
        return os.path.join(KUBECONFIG_DIR, spoke, "kubeconfig")

    if spoke:
        candidate = os.path.join(KUBECONFIG_DIR, spoke, "kubeconfig")
        if os.path.isfile(candidate):
            return candidate
    return EDGE_KUBECONFIG


mcp = FastMCP(
    "noc-openshift",
    instructions=(
        "OpenShift cluster management tools for the NOC remediation agent. "
        "Use these tools to inspect pod status, get logs, patch deployments, "
        "and trigger restarts on the edge cluster. "
        "Pass edge_site_id (edge-NN) to target a specific spoke in hub-spoke mode."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=(MCP_TRANSPORT == "streamable-http"),
)
