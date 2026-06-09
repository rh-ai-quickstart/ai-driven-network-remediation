"""Unit tests for mcp_aap tools (AAP REST API is always mocked)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from mcp_aap.tools import (
    _aap_client,
    get_job_output,
    get_job_status,
    launch_job,
    list_job_templates,
    upsert_job_template,
)


def _mock_response(status_code=200, json_data=None, text=""):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture()
def mock_ctx():
    """Create a mock httpx.Client context manager for _aap_client()."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestAapClient:
    """Tests for the _aap_client helper."""

    @patch("mcp_aap.tools.httpx.Client")
    def test_constructs_client(self, mock_client_cls):
        _ = _aap_client()
        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert "aap.aap.svc" in kwargs["base_url"]
        assert "/api/v2" in kwargs["base_url"]
        assert kwargs["timeout"] == 30


@patch("mcp_aap.tools._aap_client")
class TestListJobTemplates:
    """Tests for the list_job_templates tool."""

    def test_success(self, mock_client, mock_ctx):
        data = {
            "results": [
                {"id": 1, "name": "restart-nginx", "description": "Restart nginx", "playbook": "restart.yml"},
                {"id": 2, "name": "scale-up", "description": "", "playbook": "scale.yml"},
            ]
        }
        mock_ctx.get.return_value = _mock_response(json_data=data)
        mock_client.return_value = mock_ctx

        result = list_job_templates()
        assert result["success"] is True
        assert result["count"] == 2
        assert result["job_templates"][0]["name"] == "restart-nginx"
        assert result["job_templates"][1]["playbook"] == "scale.yml"

    def test_empty_results(self, mock_client, mock_ctx):
        mock_ctx.get.return_value = _mock_response(json_data={"results": []})
        mock_client.return_value = mock_ctx

        result = list_job_templates()
        assert result["success"] is True
        assert result["count"] == 0
        assert result["job_templates"] == []

    def test_api_error(self, mock_client, mock_ctx):
        mock_ctx.get.return_value = _mock_response(status_code=401)
        mock_client.return_value = mock_ctx

        result = list_job_templates()
        assert result["success"] is False
        assert "401" in result["error"]

    def test_connection_error(self, mock_client, mock_ctx):
        mock_ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = mock_ctx

        result = list_job_templates()
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


@patch("mcp_aap.tools._aap_client")
class TestLaunchJob:
    """Tests for the launch_job tool."""

    def test_success(self, mock_client, mock_ctx):
        search_data = {"results": [{"id": 10, "name": "restart-nginx"}]}
        launch_data = {"id": 42, "status": "pending"}
        mock_ctx.get.return_value = _mock_response(json_data=search_data)
        mock_ctx.post.return_value = _mock_response(json_data=launch_data)
        mock_client.return_value = mock_ctx

        result = launch_job(job_template_name="restart-nginx")
        assert result["success"] is True
        assert result["job_id"] == 42
        assert result["template_name"] == "restart-nginx"

    def test_with_extra_vars(self, mock_client, mock_ctx):
        search_data = {"results": [{"id": 10, "name": "restart-nginx"}]}
        launch_data = {"id": 43, "status": "pending"}
        mock_ctx.get.return_value = _mock_response(json_data=search_data)
        mock_ctx.post.return_value = _mock_response(json_data=launch_data)
        mock_client.return_value = mock_ctx

        result = launch_job(job_template_name="restart-nginx", extra_vars={"namespace": "dark-noc-edge"})
        assert result["success"] is True
        posted_payload = mock_ctx.post.call_args.kwargs.get("json", mock_ctx.post.call_args[1].get("json", {}))
        assert "extra_vars" in posted_payload

    def test_template_not_found(self, mock_client, mock_ctx):
        mock_ctx.get.return_value = _mock_response(json_data={"results": []})
        mock_client.return_value = mock_ctx

        result = launch_job(job_template_name="nonexistent")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_api_error(self, mock_client, mock_ctx):
        mock_ctx.get.return_value = _mock_response(status_code=403)
        mock_client.return_value = mock_ctx

        result = launch_job(job_template_name="restart-nginx")
        assert result["success"] is False
        assert "403" in result["error"]

    def test_connection_error(self, mock_client, mock_ctx):
        mock_ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = mock_ctx

        result = launch_job(job_template_name="restart-nginx")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


@patch("mcp_aap.tools._aap_client")
class TestUpsertJobTemplate:
    """Tests for the upsert_job_template tool."""

    def test_update_existing(self, mock_client, mock_ctx):
        existing_data = {"results": [{"id": 5, "name": "my-template"}]}
        patched_data = {"id": 5, "name": "my-template", "playbook": "fix.yml"}
        mock_ctx.get.return_value = _mock_response(json_data=existing_data)
        mock_ctx.patch.return_value = _mock_response(json_data=patched_data)
        mock_client.return_value = mock_ctx

        result = upsert_job_template(template_name="my-template", playbook="fix.yml")
        assert result["success"] is True
        assert result["created"] is False
        assert result["template_id"] == 5
        assert result["playbook"] == "fix.yml"

    def test_create_from_base(self, mock_client, mock_ctx):
        copied_data = {"id": 99}
        patched_data = {"id": 99, "name": "new-template", "playbook": "new.yml"}
        mock_ctx.get.side_effect = [
            _mock_response(json_data={"results": []}),
            _mock_response(json_data={"results": [{"id": 1, "name": "lightspeed-generate-and-run"}]}),
        ]
        mock_ctx.post.return_value = _mock_response(json_data=copied_data)
        mock_ctx.patch.return_value = _mock_response(json_data=patched_data)
        mock_client.return_value = mock_ctx

        result = upsert_job_template(template_name="new-template", playbook="new.yml")
        assert result["success"] is True
        assert result["created"] is True
        assert result["template_id"] == 99

    def test_base_template_not_found(self, mock_client, mock_ctx):
        mock_ctx.get.side_effect = [
            _mock_response(json_data={"results": []}),
            _mock_response(json_data={"results": []}),
        ]
        mock_client.return_value = mock_ctx

        result = upsert_job_template(template_name="new-template", playbook="new.yml")
        assert result["success"] is False
        assert "Base template" in result["error"]

    def test_patch_failure_idempotent(self, mock_client, mock_ctx):
        existing_data = {"results": [{"id": 5, "name": "my-template"}]}
        current_data = {"id": 5, "name": "my-template", "playbook": "fix.yml"}
        mock_ctx.get.side_effect = [
            _mock_response(json_data=existing_data),
            _mock_response(json_data=current_data),
        ]
        patch_resp = MagicMock(spec=httpx.Response)
        patch_resp.status_code = 403
        patch_resp.raise_for_status.return_value = None
        mock_ctx.patch.return_value = patch_resp
        mock_client.return_value = mock_ctx

        result = upsert_job_template(template_name="my-template", playbook="fix.yml")
        assert result["success"] is True
        assert "idempotent" in result.get("warning", "")

    def test_patch_failure_non_idempotent(self, mock_client, mock_ctx):
        existing_data = {"results": [{"id": 5, "name": "my-template"}]}
        current_data = {"id": 5, "name": "my-template", "playbook": "old.yml"}
        mock_ctx.get.side_effect = [
            _mock_response(json_data=existing_data),
            _mock_response(json_data=current_data),
        ]
        patch_resp = MagicMock(spec=httpx.Response)
        patch_resp.status_code = 403
        patch_resp.raise_for_status.return_value = None
        mock_ctx.patch.return_value = patch_resp
        mock_client.return_value = mock_ctx

        result = upsert_job_template(template_name="my-template", playbook="fix.yml")
        assert result["success"] is False
        assert "patch failed" in result["error"]

    def test_api_error(self, mock_client, mock_ctx):
        mock_ctx.get.return_value = _mock_response(status_code=500)
        mock_client.return_value = mock_ctx

        result = upsert_job_template(template_name="my-template", playbook="fix.yml")
        assert result["success"] is False
        assert "500" in result["error"]


@patch("mcp_aap.tools._aap_client")
class TestGetJobStatus:
    """Tests for the get_job_status tool."""

    def test_success(self, mock_client, mock_ctx):
        job_data = {
            "status": "successful",
            "elapsed": 12.5,
            "started": "2026-06-01T10:00:00Z",
            "finished": "2026-06-01T10:00:12Z",
            "failed": False,
            "result_traceback": "",
        }
        mock_ctx.get.return_value = _mock_response(json_data=job_data)
        mock_client.return_value = mock_ctx

        result = get_job_status(job_id=42)
        assert result["success"] is True
        assert result["job_id"] == 42
        assert result["status"] == "successful"
        assert result["elapsed"] == 12.5
        assert result["failed"] is False

    def test_failed_job(self, mock_client, mock_ctx):
        job_data = {
            "status": "failed",
            "elapsed": 3.0,
            "started": "2026-06-01T10:00:00Z",
            "finished": "2026-06-01T10:00:03Z",
            "failed": True,
            "result_traceback": "UNREACHABLE! host not found",
        }
        mock_ctx.get.return_value = _mock_response(json_data=job_data)
        mock_client.return_value = mock_ctx

        result = get_job_status(job_id=99)
        assert result["success"] is True
        assert result["failed"] is True
        assert "UNREACHABLE" in result["result_traceback"]

    def test_api_error(self, mock_client, mock_ctx):
        mock_ctx.get.return_value = _mock_response(status_code=404)
        mock_client.return_value = mock_ctx

        result = get_job_status(job_id=999)
        assert result["success"] is False
        assert result["job_id"] == 999
        assert "404" in result["error"]

    def test_connection_error(self, mock_client, mock_ctx):
        mock_ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = mock_ctx

        result = get_job_status(job_id=42)
        assert result["success"] is False
        assert result["job_id"] == 42
        assert "connection error" in result["error"].lower()


@patch("mcp_aap.tools._aap_client")
class TestGetJobOutput:
    """Tests for the get_job_output tool."""

    def test_success(self, mock_client, mock_ctx):
        output = "PLAY [all] ***\nTASK [Gathering Facts] ***\nok: [host1]\nTASK [restart] ***\nchanged: [host1]"
        mock_ctx.get.return_value = _mock_response(text=output)
        mock_client.return_value = mock_ctx

        result = get_job_output(job_id=42)
        assert result["success"] is True
        assert result["job_id"] == 42
        assert "PLAY [all]" in result["output"]
        assert result["total_lines"] == 5

    def test_truncation(self, mock_client, mock_ctx):
        lines = [f"line {i}" for i in range(100)]
        mock_ctx.get.return_value = _mock_response(text="\n".join(lines))
        mock_client.return_value = mock_ctx

        result = get_job_output(job_id=42, last_lines=10)
        assert result["total_lines"] == 100
        assert result["truncated_to"] == 10
        assert "line 90" in result["output"]
        assert "line 0" not in result["output"]

    def test_no_truncation_needed(self, mock_client, mock_ctx):
        output = "line 1\nline 2\nline 3"
        mock_ctx.get.return_value = _mock_response(text=output)
        mock_client.return_value = mock_ctx

        result = get_job_output(job_id=42, last_lines=50)
        assert result["total_lines"] == 3
        assert result["output"] == output

    def test_api_error(self, mock_client, mock_ctx):
        mock_ctx.get.return_value = _mock_response(status_code=404)
        mock_client.return_value = mock_ctx

        result = get_job_output(job_id=999)
        assert result["success"] is False
        assert result["job_id"] == 999
        assert "404" in result["error"]

    def test_connection_error(self, mock_client, mock_ctx):
        mock_ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = mock_ctx

        result = get_job_output(job_id=42)
        assert result["success"] is False
        assert "connection error" in result["error"].lower()
