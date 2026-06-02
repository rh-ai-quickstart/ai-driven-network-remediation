from unittest.mock import patch

from mcp_kafka.server import app, main
from starlette.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "OK"}


class TestMain:
    @patch("mcp_kafka.server.mcp")
    def test_main_calls_run(self, mock_mcp):
        main()
        mock_mcp.run.assert_called_once()
