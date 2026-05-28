from unittest.mock import patch

from mcp_lokistack.server import app, main
from starlette.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "OK"}


class TestMain:
    def test_main_calls_run(self):
        with patch("mcp_lokistack.server.mcp") as mock_mcp:
            main()
            mock_mcp.run.assert_called_once()
