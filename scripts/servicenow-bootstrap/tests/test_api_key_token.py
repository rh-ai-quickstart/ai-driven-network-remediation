"""Tests for ServiceNowAPIAutomation._persist_api_key_token token reuse."""

from unittest.mock import patch

from servicenow_bootstrap.create_noc_agent_api_key import ServiceNowAPIAutomation

API_KEY_SYS_ID = "sys-id-123"
HIDDEN_TOKEN = "hidden"


def _make_automation():
    automation = ServiceNowAPIAutomation.__new__(ServiceNowAPIAutomation)
    automation._fetch_api_key_token = lambda _sys_id: HIDDEN_TOKEN
    return automation


class TestPersistApiKeyToken:
    @patch("servicenow_bootstrap.create_noc_agent_api_key.load_creds_file", return_value={})
    @patch("servicenow_bootstrap.create_noc_agent_api_key.merge_creds_file")
    def test_saves_and_returns_real_token(self, mock_merge, _mock_load, capsys):
        automation = _make_automation()

        result = automation._persist_api_key_token(API_KEY_SYS_ID, "real-token-abc")

        assert result == "real-token-abc"
        mock_merge.assert_called_once_with({"api_key": "real-token-abc"})
        assert "WARNING" not in capsys.readouterr().out

    @patch(
        "servicenow_bootstrap.create_noc_agent_api_key.load_creds_file",
        return_value={"api_key": "stored-token-123"},
    )
    @patch("servicenow_bootstrap.create_noc_agent_api_key.merge_creds_file")
    def test_reuses_stored_token_without_warning(self, _mock_merge, _mock_load, capsys):
        automation = _make_automation()

        result = automation._persist_api_key_token(API_KEY_SYS_ID, HIDDEN_TOKEN)

        assert result == "stored-token-123"
        out = capsys.readouterr().out
        assert "WARNING" not in out
        assert "Could not retrieve" not in out

    @patch("servicenow_bootstrap.create_noc_agent_api_key.load_creds_file", return_value={})
    @patch("servicenow_bootstrap.create_noc_agent_api_key.merge_creds_file")
    def test_warns_when_no_stored_token(self, _mock_merge, _mock_load, capsys):
        automation = _make_automation()

        result = automation._persist_api_key_token(API_KEY_SYS_ID, HIDDEN_TOKEN)

        assert result == HIDDEN_TOKEN
        out = capsys.readouterr().out
        assert "WARNING" in out or "Could not retrieve" in out
