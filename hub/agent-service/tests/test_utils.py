import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agent_service.config import EDGE_NAMESPACE
from agent_service.utils import (
    build_launch_extra_vars,
    derive_deployment_name,
    invoke_tool,
    warm_tool_cache,
)


def _response(data, status=200, method="POST", url="http://test/v1/tool-runtime/invoke"):
    return httpx.Response(
        status,
        json=data,
        request=httpx.Request(method, url),
    )


@pytest.fixture(autouse=True)
def _mock_client():
    mock = AsyncMock()
    with patch("agent_service.utils.get_http_client", return_value=mock):
        yield mock


async def test_success_json_string(_mock_client):
    _mock_client.post.return_value = _response({"content": json.dumps({"success": True, "job_id": 1})})
    result = await invoke_tool("launch_job", {"template": "x"})
    assert result == {"success": True, "job_id": 1}
    assert "timeout" not in _mock_client.post.call_args.kwargs


async def test_explicit_tool_timeout_overrides_client_default(_mock_client):
    _mock_client.post.return_value = _response({"content": '{"success": true, "valid": true}'})
    await invoke_tool("validate_playbook", {"playbook_content": "example"}, timeout_seconds=90)
    assert _mock_client.post.call_args.kwargs["timeout"] == 90


async def test_success_content_block(_mock_client):
    _mock_client.post.return_value = _response({"content": [{"type": "text", "text": '{"ok": true}'}]})
    result = await invoke_tool("get_job_output", {})
    assert result == {"ok": True}


async def test_error_message(_mock_client):
    _mock_client.post.return_value = _response({"error_message": "boom"})
    result = await invoke_tool("launch_job", {})
    assert result == {"success": False, "error": "boom"}


async def test_unparseable_content(_mock_client):
    _mock_client.post.return_value = _response({"content": "not json {"})
    result = await invoke_tool("launch_job", {})
    assert result["success"] is False
    assert "unparseable" in result["error"]


async def test_empty_content(_mock_client):
    _mock_client.post.return_value = _response({"content": ""})
    result = await invoke_tool("launch_job", {})
    assert result == {}


async def test_warm_tool_cache_calls_list_tools(_mock_client):
    _mock_client.get.return_value = _response(
        {"data": [{"name": "tool1"}, {"name": "tool2"}]},
        method="GET",
        url="http://test/v1/tools",
    )
    result = await warm_tool_cache()
    assert result is True
    _mock_client.get.assert_called_once_with("/v1/tools")


async def test_warm_tool_cache_survives_failure(_mock_client):
    _mock_client.get.side_effect = Exception("connection refused")
    result = await warm_tool_cache()
    assert result is False


def _log_event(ns="prod", pod="nginx-abc", container="web", site="site-1"):
    from types import SimpleNamespace

    return SimpleNamespace(namespace=ns, pod_name=pod, container=container, edge_site_id=site)


class TestDeriveDeploymentName:
    @pytest.mark.parametrize(
        "pod_name, expected",
        [
            ("memory-hog-54f9fcb6cc-t6w5x", "memory-hog"),
            ("orders-api-6b7f8c9d4-x2k9z", "orders-api"),
            ("memory-hog", "memory-hog"),
            ("kafka-0", "kafka-0"),
            ("", ""),
            ("single", "single"),
        ],
    )
    def test_derive(self, pod_name, expected):
        assert derive_deployment_name(pod_name) == expected

    def test_bare_name_does_not_collapse_to_empty(self):
        assert derive_deployment_name("memory-hog") != ""


class TestBuildLaunchExtraVarsBareDeployment:
    def test_bare_pod_name_yields_nonempty_deployment_name(self):
        result = build_launch_extra_vars(
            _log_event(
                ns="dark-noc-edge",
                pod="memory-hog",
                container="memory-hog",
                site="edge-site-01",
            ),
        )
        assert result["deployment_name"] == "memory-hog"
        assert result["deployment"] == "memory-hog"
        assert result["namespace"] == "dark-noc-edge"
        assert result["pod_name"] == "memory-hog"
        assert result["container"] == "memory-hog"
        assert result["edge_site_id"] == "edge-site-01"

    def test_nginx_edge_pod_maps_to_edge_nginx_deployment(self):
        result = build_launch_extra_vars(
            _log_event(
                ns=EDGE_NAMESPACE,
                pod="nginx-edge-oom",
                container="nginx",
                site="edge-01",
            ),
        )
        assert result["deployment"] == "edge-nginx"
        assert result["deployment_name"] == "edge-nginx"

    def test_real_edge_nginx_pod_maps_to_edge_nginx(self):
        result = build_launch_extra_vars(
            _log_event(
                ns="dark-noc-edge",
                pod="edge-nginx-6b7f8c9d4-x2k9z",
                container="nginx",
                site="edge-01",
            ),
        )
        assert result["deployment"] == "edge-nginx"
        assert result["deployment_name"] == "edge-nginx"
        assert result["pod_name"] == "edge-nginx-6b7f8c9d4-x2k9z"
        assert result["edge_site_id"] == "edge-01"
