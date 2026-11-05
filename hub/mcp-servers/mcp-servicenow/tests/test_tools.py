"""Unit tests for mcp_servicenow tools (ServiceNow HTTP is always mocked)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from mcp_servicenow.tools import (
    _resolve_close_codes,
    _snow_client,
    create_incident,
    get_incident,
    resolve_incident,
    update_incident,
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


def _make_ctx():
    """Build a mock context-manager client."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# _snow_client construction
# ─────────────────────────────────────────────────────────────────────────────


class TestSnowClient:
    """Tests for the _snow_client factory."""

    @patch("mcp_servicenow.tools.SNOW_API_KEY", "")
    @patch("mcp_servicenow.tools.SNOW_USERNAME", "admin")
    @patch("mcp_servicenow.tools.SNOW_PASSWORD", "secret")
    @patch("mcp_servicenow.tools.httpx.Client")
    def test_uses_basic_auth_when_no_api_key(self, mock_client_cls):
        _snow_client()
        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert "/api/now" in kwargs["base_url"]
        assert kwargs["auth"] == ("admin", "secret")
        assert "x-sn-apikey" not in kwargs["headers"]

    @patch("mcp_servicenow.tools.SNOW_API_KEY", "snow-token-abc")
    @patch("mcp_servicenow.tools.httpx.Client")
    def test_uses_api_key_header_when_set(self, mock_client_cls):
        _snow_client()
        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["headers"]["x-sn-apikey"] == "snow-token-abc"
        assert "auth" not in kwargs


# ─────────────────────────────────────────────────────────────────────────────
# create_incident
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._snow_client")
class TestCreateIncident:
    """Tests for create_incident."""

    def test_success_resolves_caller(self, mock_client):
        caller_data = {"result": [{"sys_id": "caller-001", "name": "NOC Agent", "user_name": "noc.agent"}]}
        snow_data = {"result": {"number": "INC0010001", "sys_id": "real-123"}}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=caller_data)
        ctx.post.return_value = _mock_response(json_data=snow_data)
        mock_client.return_value = ctx

        result = create_incident(
            short_description="Pod crash",
            description="nginx CrashLoopBackOff in prod",
            priority=2,
        )
        assert result["success"] is True
        assert result["ticket_number"] == "INC0010001"
        assert result["sys_id"] == "real-123"
        assert result["priority"] == 2
        assert "slack_notification" not in result
        ctx.post.assert_called_once()

        posted_body = ctx.post.call_args.kwargs.get("json", ctx.post.call_args[1].get("json", {}))
        assert "record" not in posted_body
        assert posted_body["caller_id"] == "caller-001"

    def test_creates_caller_when_not_found(self, mock_client):
        empty_search = {"result": []}
        created_user = {"result": {"sys_id": "new-caller-001"}}
        snow_data = {"result": {"number": "INC0010002", "sys_id": "real-456"}}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=empty_search)
        ctx.post.side_effect = [
            _mock_response(json_data=created_user),
            _mock_response(json_data=snow_data),
        ]
        mock_client.return_value = ctx

        result = create_incident(short_description="New caller test", description="details")
        assert result["success"] is True
        assert ctx.post.call_count == 2

    def test_truncates_short_description(self, mock_client):
        caller_data = {"result": [{"sys_id": "caller-001"}]}
        snow_data = {"result": {"number": "INC0000002", "sys_id": "def456"}}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=caller_data)
        ctx.post.return_value = _mock_response(json_data=snow_data)
        mock_client.return_value = ctx

        long_desc = "x" * 200
        result = create_incident(short_description=long_desc, description="details")
        assert result["success"] is True
        assert len(result["short_description"]) == 160

    def test_api_error(self, mock_client):
        caller_data = {"result": [{"sys_id": "caller-001"}]}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=caller_data)
        ctx.post.return_value = _mock_response(status_code=500, text="Internal Server Error")
        mock_client.return_value = ctx

        result = create_incident(short_description="fail", description="x")
        assert result["success"] is False
        assert "500" in result["error"]

    def test_connection_error(self, mock_client):
        ctx = _make_ctx()
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = create_incident(short_description="fail", description="x")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# update_incident
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._snow_client")
class TestUpdateIncident:
    """Tests for update_incident."""

    def test_success_uses_sys_id(self, mock_client):
        lookup_data = {"result": [{"sys_id": "real-sys-001", "number": "INC0010001"}]}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        ctx.patch.return_value = _mock_response(json_data={"result": {}})
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC0010001", work_notes="fixed")
        assert result["success"] is True
        assert result["ticket_number"] == "INC0010001"
        assert result["updated_state"] == "unchanged"

        patch_url = ctx.patch.call_args[0][0]
        assert "real-sys-001" in patch_url

        patched_body = ctx.patch.call_args.kwargs.get("json", ctx.patch.call_args[1].get("json", {}))
        assert "record" not in patched_body
        assert patched_body["work_notes"] == "fixed"

    def test_with_state_change(self, mock_client):
        lookup_data = {"result": [{"sys_id": "abc123", "number": "INC0000001"}]}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        ctx.patch.return_value = _mock_response(json_data={"result": {}})
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC0000001", work_notes="Starting work", state="in_progress")
        assert result["success"] is True
        assert result["updated_state"] == "in_progress"

        patched_body = ctx.patch.call_args.kwargs.get("json", ctx.patch.call_args[1].get("json", {}))
        assert patched_body["state"] == "2"

    def test_not_found(self, mock_client):
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data={"result": []})
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC9999999", work_notes="note")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_api_error(self, mock_client):
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(status_code=404, text="Not Found")
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC9999999", work_notes="note")
        assert result["success"] is False
        assert "404" in result["error"]

    def test_connection_error(self, mock_client):
        ctx = _make_ctx()
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC0000001", work_notes="note")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# get_incident
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._snow_client")
class TestGetIncident:
    """Tests for get_incident."""

    def test_success(self, mock_client):
        lookup_data = {
            "result": [
                {
                    "sys_id": "real-001",
                    "number": "INC0010001",
                    "short_description": "Edge outage",
                    "state": "2",
                    "priority": "1",
                    "assignment_group": "Edge-Ops",
                    "sys_created_on": "2026-06-01T10:00:00Z",
                    "sys_updated_on": "2026-06-01T10:15:00Z",
                }
            ]
        }
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC0010001")
        assert result["ticket_number"] == "INC0010001"
        assert result["state"] == "In Progress"
        assert result["short_description"] == "Edge outage"

    def test_not_found(self, mock_client):
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data={"result": []})
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC9999999")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_api_error(self, mock_client):
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(status_code=404, text="Not Found")
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC9999999")
        assert result["success"] is False
        assert "404" in result["error"]

    def test_connection_error(self, mock_client):
        ctx = _make_ctx()
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC0000001")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Shared close codes
# ─────────────────────────────────────────────────────────────────────────────


def test_close_codes_match_contracts():
    repo_root = Path(__file__).resolve().parents[4]
    contracts = json.loads((repo_root / "contracts" / "servicenow-close-codes.json").read_text(encoding="utf-8"))
    packaged = json.loads(
        (repo_root / "hub/mcp-servers/mcp-servicenow/src/mcp_servicenow/close_codes.json").read_text(encoding="utf-8")
    )
    assert contracts == packaged


class TestResolveCloseCodes:
    def test_explicit_code_skips_instance_lookup(self):
        ctx = _make_ctx()
        codes = _resolve_close_codes(ctx, "Solution provided")
        assert codes == ["Solution provided"]
        ctx.get.assert_not_called()

    def test_prefers_instance_matched_fallback(self):
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(
            json_data={
                "result": [
                    {"label": "Solution provided", "value": "solution_provided"},
                ]
            }
        )
        codes = _resolve_close_codes(ctx, None)
        assert codes == ["Solution provided"]


# ─────────────────────────────────────────────────────────────────────────────
# resolve_incident
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._snow_client")
class TestResolveIncident:
    """Tests for resolve_incident."""

    def test_success_uses_sys_id(self, mock_client):
        lookup_data = {"result": [{"sys_id": "real-sys-001", "number": "INC0010001"}]}
        choice_data = {"result": [{"label": "Solved (Permanently)", "value": "solved"}]}
        ctx = _make_ctx()
        ctx.get.side_effect = [
            _mock_response(json_data=lookup_data),
            _mock_response(json_data=choice_data),
        ]
        ctx.patch.return_value = _mock_response(json_data={"result": {}})
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC0010001", resolution_notes="Fixed the root cause")
        assert result["success"] is True
        assert result["ticket_number"] == "INC0010001"
        assert result["state"] == "Resolved"
        assert result["resolution_code"] == "Solved (Permanently)"
        assert ctx.patch.call_count == 1

        patch_url = ctx.patch.call_args[0][0]
        assert "real-sys-001" in patch_url

        patched_body = ctx.patch.call_args.kwargs.get("json", ctx.patch.call_args[1].get("json", {}))
        assert "record" not in patched_body
        assert patched_body["state"] == "6"

    def test_custom_resolution_code(self, mock_client):
        lookup_data = {"result": [{"sys_id": "abc123", "number": "INC0000001"}]}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        ctx.patch.return_value = _mock_response(json_data={"result": {}})
        mock_client.return_value = ctx

        result = resolve_incident(
            ticket_number="INC0000001",
            resolution_notes="Workaround applied",
            resolution_code="Solved (Workaround)",
        )
        assert result["success"] is True
        assert result["resolution_code"] == "Solved (Workaround)"
        assert ctx.patch.call_count == 1

    def test_close_code_fallback(self, mock_client):
        lookup_data = {"result": [{"sys_id": "abc123", "number": "INC0000001"}]}
        ctx = _make_ctx()
        ctx.get.side_effect = [
            _mock_response(json_data=lookup_data),
            _mock_response(status_code=403, text="Forbidden"),
        ]
        ctx.patch.side_effect = [
            _mock_response(status_code=403, text="Invalid close_code"),
            _mock_response(json_data={"result": {}}),
        ]
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC0000001", resolution_notes="Fixed")
        assert result["success"] is True
        assert result["resolution_code"] == "Solution provided"
        assert ctx.patch.call_count == 2

    def test_explicit_resolution_code_no_fallback(self, mock_client):
        lookup_data = {"result": [{"sys_id": "abc123", "number": "INC0000001"}]}
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        ctx.patch.return_value = _mock_response(status_code=403, text="Invalid close_code")
        mock_client.return_value = ctx

        result = resolve_incident(
            ticket_number="INC0000001",
            resolution_notes="Fixed",
            resolution_code="Solved (Permanently)",
        )
        assert result["success"] is False
        assert "403" in result["error"]
        assert ctx.patch.call_count == 1

    def test_not_found(self, mock_client):
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(json_data={"result": []})
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC9999999", resolution_notes="x")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_api_error(self, mock_client):
        ctx = _make_ctx()
        ctx.get.return_value = _mock_response(status_code=500, text="Server Error")
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC0000001", resolution_notes="x")
        assert result["success"] is False
        assert "500" in result["error"]

    def test_connection_error(self, mock_client):
        ctx = _make_ctx()
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC0000001", resolution_notes="x")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()
