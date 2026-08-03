"""Unit tests for multi-cluster kubeconfig resolution (C6)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_openshift.config import resolve_kubeconfig, site_id_to_spoke_name
from mcp_openshift.tools import get_pods


@pytest.mark.parametrize(
    ("site_id", "prefix", "expected"),
    [
        ("edge-01", "edge-site", "edge-site-01"),
        ("edge-02", "edge-site", "edge-site-02"),
        ("edge-1", "edge-site", "edge-site-01"),
        ("edge-site-01", "edge-site", "edge-site-01"),
        ("edge-site-1", "edge-site", "edge-site-01"),
        ("edge-01", "spoke", "spoke-01"),
        ("spoke-01", "spoke", "spoke-01"),
        ("", "edge-site", ""),
        ("unknown", "edge-site", ""),
        ("  edge-03  ", "edge-site", "edge-site-03"),
        # Reject free-form / traversal values (never echo into kubeconfig paths).
        ("../../etc/passwd", "edge-site", ""),
        ("edge-site-01/../edge-site-02", "edge-site", ""),
        ("other-cluster", "edge-site", ""),
        ("edge-01", "../evil", ""),
    ],
)
def test_site_id_to_spoke_name(site_id, prefix, expected):
    assert site_id_to_spoke_name(site_id, prefix=prefix) == expected


def test_resolve_kubeconfig_falls_back_to_edge_default(tmp_path):
    with (
        patch("mcp_openshift.config.EDGE_KUBECONFIG", "/kubeconfig/kubeconfig"),
        patch("mcp_openshift.config.KUBECONFIG_DIR", str(tmp_path)),
        patch("mcp_openshift.config.SPOKE_NAME_PREFIX", "edge-site"),
        patch("mcp_openshift.config.DEPLOYMENT_MODE", "single-cluster"),
    ):
        assert resolve_kubeconfig(None) == "/kubeconfig/kubeconfig"
        assert resolve_kubeconfig("") == "/kubeconfig/kubeconfig"
        assert resolve_kubeconfig("unknown") == "/kubeconfig/kubeconfig"
        assert resolve_kubeconfig("edge-01") == "/kubeconfig/kubeconfig"


def test_resolve_kubeconfig_uses_spoke_mount(tmp_path):
    spoke_dir = tmp_path / "edge-site-01"
    spoke_dir.mkdir()
    kc = spoke_dir / "kubeconfig"
    kc.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")

    with (
        patch("mcp_openshift.config.EDGE_KUBECONFIG", "/kubeconfig/kubeconfig"),
        patch("mcp_openshift.config.KUBECONFIG_DIR", str(tmp_path)),
        patch("mcp_openshift.config.SPOKE_NAME_PREFIX", "edge-site"),
        patch("mcp_openshift.config.DEPLOYMENT_MODE", "single-cluster"),
    ):
        assert resolve_kubeconfig("edge-01") == str(kc)
        assert resolve_kubeconfig("edge-site-01") == str(kc)
        assert resolve_kubeconfig("edge-02") == "/kubeconfig/kubeconfig"


def test_resolve_kubeconfig_hub_spoke_no_single_cluster_fallback(tmp_path):
    spoke_dir = tmp_path / "edge-site-01"
    spoke_dir.mkdir()
    kc = spoke_dir / "kubeconfig"
    kc.write_text("kind: Config\n", encoding="utf-8")

    with (
        patch("mcp_openshift.config.EDGE_KUBECONFIG", "/kubeconfig/kubeconfig"),
        patch("mcp_openshift.config.KUBECONFIG_DIR", str(tmp_path)),
        patch("mcp_openshift.config.SPOKE_NAME_PREFIX", "edge-site"),
        patch("mcp_openshift.config.DEPLOYMENT_MODE", "hub-spoke"),
    ):
        assert resolve_kubeconfig("edge-01") == str(kc)
        assert resolve_kubeconfig("edge-1") == str(kc)
        # Missing site must not fall back to the unmounted single-cluster path.
        missing = resolve_kubeconfig("edge-02")
        assert missing == str(tmp_path / "edge-site-02" / "kubeconfig")
        assert missing != "/kubeconfig/kubeconfig"
        unspecified = resolve_kubeconfig(None)
        assert unspecified.endswith("unspecified-edge-site/kubeconfig")
        assert unspecified != "/kubeconfig/kubeconfig"


@patch("mcp_openshift.tools._run_oc")
def test_get_pods_passes_resolved_spoke_kubeconfig(mock_oc, tmp_path):
    spoke_dir = tmp_path / "edge-site-01"
    spoke_dir.mkdir()
    kc = spoke_dir / "kubeconfig"
    kc.write_text("kind: Config\n", encoding="utf-8")

    mock_oc.return_value = {
        "stdout": '{"items": []}',
        "stderr": "",
        "returncode": 0,
        "success": True,
    }

    with patch(
        "mcp_openshift.tools.resolve_kubeconfig", return_value=str(kc)
    ) as mock_resolve:
        result = get_pods(namespace="dark-noc-edge", edge_site_id="edge-01")

    assert result["count"] == 0
    mock_resolve.assert_called_once_with("edge-01")
    assert mock_oc.call_args.kwargs["kubeconfig"] == str(kc)


@patch("mcp_openshift.tools._run_oc")
def test_get_pods_default_uses_empty_site(mock_oc):
    mock_oc.return_value = {
        "stdout": '{"items": []}',
        "stderr": "",
        "returncode": 0,
        "success": True,
    }
    with patch(
        "mcp_openshift.tools.resolve_kubeconfig",
        return_value="/kubeconfig/kubeconfig",
    ) as mock_resolve:
        get_pods()
    mock_resolve.assert_called_once_with(None)
    assert mock_oc.call_args.kwargs["kubeconfig"] == "/kubeconfig/kubeconfig"
