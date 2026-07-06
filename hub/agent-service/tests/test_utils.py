import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agent_service.utils import invoke_tool


def _response(data, status=200):
    return httpx.Response(
        status,
        json=data,
        request=httpx.Request("POST", "http://test/v1/tool-runtime/invoke"),
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
