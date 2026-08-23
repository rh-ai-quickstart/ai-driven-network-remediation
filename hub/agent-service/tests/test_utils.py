import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agent_service.utils import (
    _normalize_component_name,
    build_launch_extra_vars,
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


class TestBuildLaunchExtraVarsGrounding:
    def test_short_component_does_not_match_substring(self):
        result = build_launch_extra_vars(
            _log_event(),
            {"affected_component": "app"},
            "checking apps/v1 deployments in application namespace",
        )
        assert "deployment_name" not in result

    def test_exact_word_component_matches(self):
        result = build_launch_extra_vars(
            _log_event(),
            {"affected_component": "nginx"},
            "pod nginx is crashlooping with OOMKilled",
        )
        assert result["deployment_name"] == "nginx"

    def test_site_id_does_not_match_substring(self):
        result = build_launch_extra_vars(
            _log_event(),
            {"edge_site_id": "site"},
            "multisite deployment reported errors on website cluster",
        )
        assert result["edge_site_id"] == "site-1"

    def test_namespace_not_accepted_as_edge_site_id(self):
        result = build_launch_extra_vars(
            _log_event(ns="edge-site-01", site="local-cluster"),
            {"edge_site_id": "edge-site-01"},
            "namespace edge-site-01 has high CPU usage",
        )
        assert result["edge_site_id"] == "local-cluster"

    def test_edge_site_id_overlaid_when_stamped_in_evidence(self):
        result = build_launch_extra_vars(
            _log_event(site="edge-site-01"),
            {"edge_site_id": "edge-site-02"},
            "Edge site: edge-site-02\nPod: myapp\n  limits: cpu: 500m",
        )
        assert result["edge_site_id"] == "edge-site-02"

    def test_edge_site_id_not_overlaid_when_absent_from_evidence(self):
        result = build_launch_extra_vars(
            _log_event(site="edge-site-01"),
            {"edge_site_id": "edge-site-02"},
            "Pod: myapp is crashlooping with OOMKilled",
        )
        assert result["edge_site_id"] == "edge-site-01"


class TestNormalizeComponentName:
    @pytest.mark.parametrize(
        "component, expected",
        [
            ("deployment/orders-api (edge-site-02)", "orders-api"),
            ("nginx", "nginx"),
            ("Pod/My-App", "my-app"),
            ("", ""),
            ("web-frontend service (edge-site-01)", "web-frontend"),
        ],
    )
    def test_normalize(self, component, expected):
        assert _normalize_component_name(component) == expected
