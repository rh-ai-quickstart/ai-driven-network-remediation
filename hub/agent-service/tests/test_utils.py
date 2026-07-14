import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agent_service.utils import invoke_tool, warm_tool_cache


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
