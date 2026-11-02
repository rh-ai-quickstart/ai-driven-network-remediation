"""Unit tests for probes.py: generic HTTP reachability probe."""

import httpx
import pytest
import respx
from shared.probes import probe_http

URL = "http://example-service:8000/health"


class TestProbeHttp:
    @pytest.mark.asyncio
    @respx.mock
    async def test_reachable_on_200(self):
        respx.get(URL).mock(return_value=httpx.Response(200))
        result = await probe_http(URL)
        assert result == {"status": "up", "http_code": 200, "reachable": True}

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308, 401, 403, 404, 405])
    async def test_reachable_on_redirect_auth_and_not_found_codes(self, status_code):
        respx.get(URL).mock(return_value=httpx.Response(status_code))
        result = await probe_http(URL)
        assert result["reachable"] is True
        assert result["status"] == "up"

    @pytest.mark.asyncio
    @respx.mock
    async def test_unreachable_on_server_error(self):
        respx.get(URL).mock(return_value=httpx.Response(500))
        result = await probe_http(URL)
        assert result == {"status": "http-500", "http_code": 500, "reachable": False}

    @pytest.mark.asyncio
    @respx.mock
    async def test_unreachable_on_connection_error(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("connection refused"))
        result = await probe_http(URL)
        assert result == {"status": "down", "http_code": None, "reachable": False}

    @pytest.mark.asyncio
    @respx.mock
    async def test_passes_verify_through_to_client(self):
        """Callers (each service) supply their own SSL_VERIFY value explicitly,
        since this package has no config module of its own."""
        respx.get(URL).mock(return_value=httpx.Response(200))
        result = await probe_http(URL, verify=False)
        assert result["reachable"] is True
