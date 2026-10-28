"""Offline checks that disabled MCP services are excluded from the agent."""

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

HUB = next(parent for parent in Path(__file__).resolve().parents if parent.name == "hub")
NETWORK_CHART = HUB / "helm/charts/network"
_ENV_ENTRY = re.compile(r'- name: ENABLED_MCP_SERVERS\n\s+value: "([^"]*)"')
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI not available")


def _agent_servers(*helm_args):
    command = [
        "helm",
        "template",
        "hub",
        str(NETWORK_CHART),
        "--show-only",
        "templates/agent-service.yaml",
        "--set",
        "global.frontendAuth.enabled=false,global.routes.enabled=false",
        *helm_args,
    ]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    enabled_servers = _ENV_ENTRY.search(rendered)
    assert enabled_servers is not None
    return set(filter(None, enabled_servers.group(1).split(",")))


@pytest.mark.parametrize("server", ["noc-openshift", "noc-lokistack", "noc-kafka", "noc-aap", "noc-servicenow"])
def test_disabled_mcp_server_is_excluded(server):
    enabled_servers = _agent_servers("--set", f"mcp-servers.mcp-servers.{server}.enabled=false")
    assert server not in enabled_servers
    assert "noc-aap" in enabled_servers or server == "noc-aap"


def test_disabling_all_mcp_servers_produces_empty_inventory():
    assert _agent_servers("--set", "mcp-servers.enabled=false") == set()


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_service_flags_control_mcp_deployment_registration_and_agent_tools(enabled):
    command = [
        "make",
        "--no-print-directory",
        "-s",
        "-f",
        "Makefile",
        "-f",
        "-",
        "print-tool-flags",
        f"ENABLE_LOKISTACK={enabled}",
        f"ENABLE_KAFKA={enabled}",
        "ENABLE_NETWORK_REMEDIATION=true",
        "ENABLE_LIGHTSPEED=false",
        "ENABLE_AAP_MOCK=true",
    ]
    makefile = (
        "print-tool-flags:\n"
        "\t$(info $(helm_lokistack_args) $(helm_infra_args) $(helm_network_remediation_mcp_args))\n"
        "\t@true\n"
    )
    flags = subprocess.run(command, cwd=HUB.parent, input=makefile, capture_output=True, text=True, check=True).stdout
    enabled_servers = _agent_servers(*(flag.removeprefix("network.") for flag in shlex.split(flags)))
    for server in ("noc-lokistack", "noc-kafka"):
        assert (server in enabled_servers) == (enabled == "true")
        assert (f"llama-stack.mcp-servers.{server}.uri=" in flags) == (enabled == "true")
    assert {"noc-openshift", "noc-aap", "noc-servicenow"} <= enabled_servers
