"""Unit tests for mcp_servicenow tools (ServiceNow + Slack HTTP is always mocked)."""

from unittest.mock import MagicMock, patch

import httpx
from mcp_servicenow.tools import (
    _is_real_servicenow,
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


# ─────────────────────────────────────────────────────────────────────────────
# _is_real_servicenow mode detection
# ─────────────────────────────────────────────────────────────────────────────


class TestIsRealServicenow:
    """Tests for the _is_real_servicenow helper."""

    @patch("mcp_servicenow.tools.SNOW_MODE", "real")
    def test_real_mode(self):
        assert _is_real_servicenow() is True

    @patch("mcp_servicenow.tools.SNOW_MODE", "mock")
    def test_mock_mode(self):
        assert _is_real_servicenow() is False

    @patch("mcp_servicenow.tools.SNOW_MODE", "auto")
    @patch("mcp_servicenow.tools.SNOW_USERNAME", "admin")
    @patch("mcp_servicenow.tools.SNOW_PASSWORD", "secret")
    def test_auto_mode_with_credentials(self):
        assert _is_real_servicenow() is True

    @patch("mcp_servicenow.tools.SNOW_MODE", "auto")
    @patch("mcp_servicenow.tools.SNOW_USERNAME", "")
    @patch("mcp_servicenow.tools.SNOW_PASSWORD", "")
    def test_auto_mode_without_credentials(self):
        assert _is_real_servicenow() is False


# ─────────────────────────────────────────────────────────────────────────────
# _snow_client construction
# ─────────────────────────────────────────────────────────────────────────────


class TestSnowClient:
    """Tests for the _snow_client factory."""

    @patch("mcp_servicenow.tools._is_real_servicenow", return_value=False)
    @patch("mcp_servicenow.tools.httpx.Client")
    def test_mock_mode_uses_api_key(self, mock_client_cls, _mock_real):
        _snow_client()
        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert "/api/now" in kwargs["base_url"]
        assert kwargs["auth"] is None
        assert "X-API-Key" in kwargs["headers"]

    @patch("mcp_servicenow.tools._is_real_servicenow", return_value=True)
    @patch("mcp_servicenow.tools.SNOW_USERNAME", "admin")
    @patch("mcp_servicenow.tools.SNOW_PASSWORD", "secret")
    @patch("mcp_servicenow.tools.httpx.Client")
    def test_real_mode_uses_basic_auth(self, mock_client_cls, _mock_real):
        _snow_client()
        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["auth"] == ("admin", "secret")


# ─────────────────────────────────────────────────────────────────────────────
# create_incident — mock mode
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=False)
@patch("mcp_servicenow.tools._notify_slack_ticket_created")
@patch("mcp_servicenow.tools._snow_client")
class TestCreateIncidentMockMode:
    """Tests for create_incident in mock mode."""

    def test_success(self, mock_client, mock_slack, _mock_real):
        snow_data = {"number": "INC0000001", "sys_id": "abc123"}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post.return_value = _mock_response(json_data={"record": snow_data})
        mock_client.return_value = ctx
        mock_slack.return_value = {"sent": True, "ts": "123"}

        result = create_incident(
            short_description="Pod crash",
            description="nginx CrashLoopBackOff in prod",
            priority=2,
        )
        assert result["success"] is True
        assert result["ticket_number"] == "INC0000001"
        assert result["sys_id"] == "abc123"
        assert result["priority"] == 2
        assert result["slack_notification"]["sent"] is True

        posted_body = ctx.post.call_args.kwargs.get("json", ctx.post.call_args[1].get("json", {}))
        assert "record" in posted_body

    def test_truncates_short_description(self, mock_client, mock_slack, _mock_real):
        snow_data = {"number": "INC0000002", "sys_id": "def456"}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post.return_value = _mock_response(json_data={"record": snow_data})
        mock_client.return_value = ctx
        mock_slack.return_value = {"sent": False, "reason": "missing_token"}

        long_desc = "x" * 200
        result = create_incident(short_description=long_desc, description="details")
        assert result["success"] is True
        assert len(result["short_description"]) == 160

    def test_api_error(self, mock_client, mock_slack, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post.return_value = _mock_response(status_code=500, text="Internal Server Error")
        mock_client.return_value = ctx

        result = create_incident(short_description="fail", description="x")
        assert result["success"] is False
        assert "500" in result["error"]

    def test_connection_error(self, mock_client, mock_slack, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = create_incident(short_description="fail", description="x")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# create_incident — real mode
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=True)
@patch("mcp_servicenow.tools._notify_slack_ticket_created")
@patch("mcp_servicenow.tools._snow_client")
class TestCreateIncidentRealMode:
    """Tests for create_incident in real ServiceNow mode."""

    def test_success_resolves_caller(self, mock_client, mock_slack, _mock_real):
        caller_data = {"result": [{"sys_id": "caller-001", "name": "NOC Agent", "user_name": "noc.agent"}]}
        snow_data = {"result": {"number": "INC0010001", "sys_id": "real-123"}}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data=caller_data)
        ctx.post.return_value = _mock_response(json_data=snow_data)
        mock_client.return_value = ctx
        mock_slack.return_value = {"sent": False, "reason": "missing_token"}

        result = create_incident(short_description="Real incident", description="details")
        assert result["success"] is True
        assert result["ticket_number"] == "INC0010001"

        posted_body = ctx.post.call_args.kwargs.get("json", ctx.post.call_args[1].get("json", {}))
        assert "record" not in posted_body
        assert posted_body["caller_id"] == "caller-001"

    def test_creates_caller_when_not_found(self, mock_client, mock_slack, _mock_real):
        empty_search = {"result": []}
        created_user = {"result": {"sys_id": "new-caller-001"}}
        snow_data = {"result": {"number": "INC0010002", "sys_id": "real-456"}}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data=empty_search)
        ctx.post.side_effect = [
            _mock_response(json_data=created_user),
            _mock_response(json_data=snow_data),
        ]
        mock_client.return_value = ctx
        mock_slack.return_value = {"sent": False, "reason": "missing_token"}

        result = create_incident(short_description="New caller test", description="details")
        assert result["success"] is True
        assert ctx.post.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# update_incident
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=False)
@patch("mcp_servicenow.tools._snow_client")
class TestUpdateIncidentMockMode:
    """Tests for update_incident in mock mode."""

    def test_success(self, mock_client, _mock_real):
        incident_record = {"sys_id": "abc123", "number": "INC0000001"}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data={"record": incident_record})
        ctx.patch.return_value = _mock_response(json_data={"record": incident_record})
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC0000001", work_notes="Investigating")
        assert result["success"] is True
        assert result["ticket_number"] == "INC0000001"
        assert result["updated_state"] == "unchanged"

    def test_with_state_change(self, mock_client, _mock_real):
        incident_record = {"sys_id": "abc123", "number": "INC0000001"}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data={"record": incident_record})
        ctx.patch.return_value = _mock_response(json_data={"record": incident_record})
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC0000001", work_notes="Starting work", state="in_progress")
        assert result["success"] is True
        assert result["updated_state"] == "in_progress"

        patched_body = ctx.patch.call_args.kwargs.get("json", ctx.patch.call_args[1].get("json", {}))
        assert "record" in patched_body
        assert patched_body["record"]["state"] == "2"

    def test_api_error(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(status_code=404, text="Not Found")
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC9999999", work_notes="note")
        assert result["success"] is False
        assert "404" in result["error"]

    def test_connection_error(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC0000001", work_notes="note")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=True)
@patch("mcp_servicenow.tools._snow_client")
class TestUpdateIncidentRealMode:
    """Tests for update_incident in real ServiceNow mode."""

    def test_success_uses_sys_id(self, mock_client, _mock_real):
        lookup_data = {"result": [{"sys_id": "real-sys-001", "number": "INC0010001"}]}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        ctx.patch.return_value = _mock_response(json_data={"result": {}})
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC0010001", work_notes="fixed")
        assert result["success"] is True

        patch_url = ctx.patch.call_args[0][0]
        assert "real-sys-001" in patch_url

        patched_body = ctx.patch.call_args.kwargs.get("json", ctx.patch.call_args[1].get("json", {}))
        assert "record" not in patched_body
        assert patched_body["work_notes"] == "fixed"

    def test_not_found(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data={"result": []})
        mock_client.return_value = ctx

        result = update_incident(ticket_number="INC9999999", work_notes="note")
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# get_incident
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=False)
@patch("mcp_servicenow.tools._snow_client")
class TestGetIncidentMockMode:
    """Tests for get_incident in mock mode."""

    def test_success(self, mock_client, _mock_real):
        incident = {
            "record": {
                "short_description": "Pod crash",
                "state": "1",
                "priority": "2",
                "assignment_group": "NOC-Team",
                "sys_created_on": "2026-06-01T10:00:00Z",
                "sys_updated_on": "2026-06-01T10:05:00Z",
            }
        }
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data=incident)
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC0000001")
        assert result["ticket_number"] == "INC0000001"
        assert result["state"] == "New"
        assert result["short_description"] == "Pod crash"

    def test_api_error(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(status_code=404, text="Not Found")
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC9999999")
        assert result["success"] is False
        assert "404" in result["error"]

    def test_connection_error(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC0000001")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=True)
@patch("mcp_servicenow.tools._snow_client")
class TestGetIncidentRealMode:
    """Tests for get_incident in real ServiceNow mode."""

    def test_success(self, mock_client, _mock_real):
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
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC0010001")
        assert result["ticket_number"] == "INC0010001"
        assert result["state"] == "In Progress"
        assert result["short_description"] == "Edge outage"

    def test_not_found(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data={"result": []})
        mock_client.return_value = ctx

        result = get_incident(ticket_number="INC9999999")
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# resolve_incident
# ─────────────────────────────────────────────────────────────────────────────


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=False)
@patch("mcp_servicenow.tools._snow_client")
class TestResolveIncidentMockMode:
    """Tests for resolve_incident in mock mode."""

    def test_success(self, mock_client, _mock_real):
        incident_record = {"sys_id": "abc123", "number": "INC0000001"}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data={"record": incident_record})
        ctx.patch.return_value = _mock_response(json_data={"record": incident_record})
        mock_client.return_value = ctx

        result = resolve_incident(
            ticket_number="INC0000001",
            resolution_notes="Restarted pod successfully",
        )
        assert result["success"] is True
        assert result["ticket_number"] == "INC0000001"
        assert result["state"] == "Resolved"
        assert result["resolution_code"] == "Solved (Permanently)"

    def test_custom_resolution_code(self, mock_client, _mock_real):
        incident_record = {"sys_id": "abc123", "number": "INC0000001"}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data={"record": incident_record})
        ctx.patch.return_value = _mock_response(json_data={"record": incident_record})
        mock_client.return_value = ctx

        result = resolve_incident(
            ticket_number="INC0000001",
            resolution_notes="Workaround applied",
            resolution_code="Solved (Workaround)",
        )
        assert result["success"] is True
        assert result["resolution_code"] == "Solved (Workaround)"

    def test_api_error(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(status_code=500, text="Server Error")
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC0000001", resolution_notes="x")
        assert result["success"] is False
        assert "500" in result["error"]

    def test_connection_error(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC0000001", resolution_notes="x")
        assert result["success"] is False
        assert "connection error" in result["error"].lower()


@patch("mcp_servicenow.tools._is_real_servicenow", return_value=True)
@patch("mcp_servicenow.tools._snow_client")
class TestResolveIncidentRealMode:
    """Tests for resolve_incident in real ServiceNow mode."""

    def test_success_uses_sys_id(self, mock_client, _mock_real):
        lookup_data = {"result": [{"sys_id": "real-sys-001", "number": "INC0010001"}]}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data=lookup_data)
        ctx.patch.return_value = _mock_response(json_data={"result": {}})
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC0010001", resolution_notes="Fixed the root cause")
        assert result["success"] is True

        patch_url = ctx.patch.call_args[0][0]
        assert "real-sys-001" in patch_url

        patched_body = ctx.patch.call_args.kwargs.get("json", ctx.patch.call_args[1].get("json", {}))
        assert "record" not in patched_body
        assert patched_body["state"] == "6"

    def test_not_found(self, mock_client, _mock_real):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get.return_value = _mock_response(json_data={"result": []})
        mock_client.return_value = ctx

        result = resolve_incident(ticket_number="INC9999999", resolution_notes="x")
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Slack notification
# ─────────────────────────────────────────────────────────────────────────────


class TestNotifySlackTicketCreated:
    """Tests for the _notify_slack_ticket_created helper."""

    @patch("mcp_servicenow.tools.SLACK_BOT_TOKEN", "")
    def test_missing_token_returns_not_sent(self):
        from mcp_servicenow.tools import _notify_slack_ticket_created

        result = _notify_slack_ticket_created({"ticket_number": "INC0000001"})
        assert result["sent"] is False
        assert result["reason"] == "missing_token"

    @patch("mcp_servicenow.tools.SLACK_BOT_TOKEN", "xoxb-test-token")
    @patch("mcp_servicenow.tools.httpx.Client")
    def test_success(self, mock_client_cls):
        from mcp_servicenow.tools import _notify_slack_ticket_created

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post.return_value = _mock_response(json_data={"ok": True, "ts": "1234.5678"})
        mock_client_cls.return_value = ctx

        result = _notify_slack_ticket_created(
            {"ticket_number": "INC0000001", "priority": 2, "short_description": "test"}
        )
        assert result["sent"] is True
        assert result["ts"] == "1234.5678"

    @patch("mcp_servicenow.tools.SLACK_BOT_TOKEN", "xoxb-test-token")
    @patch("mcp_servicenow.tools.httpx.Client")
    def test_slack_api_error(self, mock_client_cls):
        from mcp_servicenow.tools import _notify_slack_ticket_created

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post.return_value = _mock_response(json_data={"ok": False, "error": "channel_not_found"})
        mock_client_cls.return_value = ctx

        result = _notify_slack_ticket_created({"ticket_number": "INC0000001"})
        assert result["sent"] is False
        assert result["reason"] == "channel_not_found"

    @patch("mcp_servicenow.tools.SLACK_BOT_TOKEN", "xoxb-test-token")
    @patch("mcp_servicenow.tools.httpx.Client")
    def test_connection_error(self, mock_client_cls):
        from mcp_servicenow.tools import _notify_slack_ticket_created

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = ctx

        result = _notify_slack_ticket_created({"ticket_number": "INC0000001"})
        assert result["sent"] is False
        assert "Connection refused" in result["reason"]
