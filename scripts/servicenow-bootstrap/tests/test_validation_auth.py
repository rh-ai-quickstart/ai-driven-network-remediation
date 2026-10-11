"""Tests for validation auth resolution."""

from unittest.mock import patch

from servicenow_bootstrap.setup_validations import _resolve_validation_auth


class TestResolveValidationAuth:
    @patch(
        "servicenow_bootstrap.setup_validations.load_creds_file",
        return_value={"user_id": "noc_agent", "password": "from-file"},
    )
    def test_prefers_api_key_from_env(self, _mock_creds, monkeypatch):
        monkeypatch.setenv("SERVICENOW_API_KEY", "env-api-key")

        auth = _resolve_validation_auth()

        assert auth == {"api_key": "env-api-key", "username": None, "password": None}

    @patch("servicenow_bootstrap.setup_validations.load_creds_file", return_value={})
    def test_basic_auth_from_env_when_no_api_key(self, _mock_creds, monkeypatch):
        monkeypatch.delenv("SERVICENOW_API_KEY", raising=False)
        monkeypatch.setenv("SERVICENOW_USERNAME", "noc_agent")
        monkeypatch.setenv("SERVICENOW_PASSWORD", "from-env")

        auth = _resolve_validation_auth()

        assert auth == {"api_key": None, "username": "noc_agent", "password": "from-env"}

    @patch(
        "servicenow_bootstrap.setup_validations.load_creds_file",
        return_value={"user_id": "noc_agent", "password": "from-file"},
    )
    def test_basic_auth_from_creds_file(self, _mock_creds, monkeypatch):
        monkeypatch.delenv("SERVICENOW_API_KEY", raising=False)
        monkeypatch.delenv("SERVICENOW_USERNAME", raising=False)
        monkeypatch.delenv("SERVICENOW_PASSWORD", raising=False)

        auth = _resolve_validation_auth()

        assert auth == {"api_key": None, "username": "noc_agent", "password": "from-file"}
