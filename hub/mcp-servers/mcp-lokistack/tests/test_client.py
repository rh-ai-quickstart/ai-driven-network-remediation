import time
from unittest.mock import patch

import httpx
import pytest
import respx
from mcp_lokistack.client import (
    _base_url_for,
    _build_client,
    _close_all,
    _get_client,
    _invalidate_client,
    _is_retryable,
    _tls_verify,
    get_label_values,
    loki_label_values,
    loki_query,
    loki_query_range,
)


class TestIsRetryable:
    def test_429(self):
        resp = httpx.Response(429, request=httpx.Request("GET", "http://test"))
        exc = httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        assert _is_retryable(exc) is True

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_server_errors(self, status):
        resp = httpx.Response(status, request=httpx.Request("GET", "http://test"))
        exc = httpx.HTTPStatusError("error", request=resp.request, response=resp)
        assert _is_retryable(exc) is True

    def test_400_not_retryable(self):
        resp = httpx.Response(400, request=httpx.Request("GET", "http://test"))
        exc = httpx.HTTPStatusError("bad req", request=resp.request, response=resp)
        assert _is_retryable(exc) is False

    def test_connect_error(self):
        assert _is_retryable(httpx.ConnectError("refused")) is True

    def test_read_timeout(self):
        assert _is_retryable(httpx.ReadTimeout("timeout")) is True

    def test_other_exception(self):
        assert _is_retryable(ValueError("nope")) is False


class TestTlsVerify:
    def test_default_no_verify(self):
        assert _tls_verify() is False

    def test_ca_cert_path(self, monkeypatch):
        from mcp_lokistack import config

        monkeypatch.setattr(config, "LOKI_CA_CERT_PATH", "/etc/certs/ca.crt")
        assert _tls_verify() == "/etc/certs/ca.crt"


class TestBaseUrlFor:
    def test_logs_service(self):
        url = _base_url_for("logs", "application")
        assert "/api/logs/v1/application/loki/api/v1" in url

    def test_ruler_service(self):
        url = _base_url_for("ruler", "")
        assert url.endswith("/loki/api/v1")
        assert "/api/logs/v1" not in url

    def test_unknown_service_raises(self):
        with pytest.raises(ValueError, match="Unknown service"):
            _base_url_for("bogus", "application")


class TestBuildClient:
    def test_base_url(self):
        client = _build_client("logs", "application")
        assert "/api/logs/v1/application/loki/api/v1" in str(client.base_url)
        client.close()

    def test_base_url_ruler(self):
        client = _build_client("ruler", "")
        assert "/loki/api/v1" in str(client.base_url)
        assert "/api/logs/v1" not in str(client.base_url)
        client.close()

    def test_auth_header(self):
        client = _build_client("logs", "infrastructure")
        assert client.headers["authorization"] == "Bearer test-token"
        client.close()

    def test_no_token(self, monkeypatch):
        from mcp_lokistack import config

        monkeypatch.setattr(config, "LOKI_TOKEN", "")
        monkeypatch.setattr(config, "LOKI_TOKEN_PATH", "")
        client = _build_client("logs", "application")
        assert "authorization" not in client.headers
        client.close()


class TestClientCache:
    def test_get_client_returns_same_instance(self):
        c1 = _get_client("logs", "application")
        c2 = _get_client("logs", "application")
        assert c1 is c2

    def test_different_tenants_different_clients(self):
        c1 = _get_client("logs", "application")
        c2 = _get_client("logs", "infrastructure")
        assert c1 is not c2

    def test_different_services_different_clients(self):
        c1 = _get_client("logs", "application")
        c2 = _get_client("ruler", "")
        assert c1 is not c2

    def test_invalidate_removes_client(self):
        c1 = _get_client("logs", "application")
        _invalidate_client("logs", "application")
        c2 = _get_client("logs", "application")
        assert c1 is not c2

    def test_close_all_clears_cache(self):
        _get_client("logs", "application")
        _get_client("logs", "infrastructure")
        _close_all()
        c = _get_client("logs", "application")
        assert c is not None

    def test_stale_client_evicted(self):
        c1 = _get_client("logs", "application")
        with patch("mcp_lokistack.client.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 600
            c2 = _get_client("logs", "application")
        assert c1 is not c2


class TestExecuteWithRetry:
    @respx.mock
    def test_connect_error_invalidates_client(self):
        respx.get("http://localhost:3100/api/logs/v1/application" "/loki/api/v1/query_range").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(httpx.ConnectError):
            loki_query_range("application", {"query": "{}"})

    @respx.mock
    def test_read_timeout_invalidates_client(self):
        respx.get("http://localhost:3100/api/logs/v1/application" "/loki/api/v1/query_range").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )
        with pytest.raises(httpx.ReadTimeout):
            loki_query_range("application", {"query": "{}"})


class TestLokiQueryRange:
    @respx.mock
    def test_success(self):
        url = "http://localhost:3100/api/logs/v1/application" "/loki/api/v1/query_range"
        respx.get(url).mock(return_value=httpx.Response(200, json={"status": "success", "data": {}}))
        result = loki_query_range("application", {"query": "{}"})
        assert result["status"] == "success"

    @respx.mock
    def test_http_error(self):
        url = "http://localhost:3100/api/logs/v1/application" "/loki/api/v1/query_range"
        respx.get(url).mock(return_value=httpx.Response(400, text="bad request"))
        with pytest.raises(httpx.HTTPStatusError):
            loki_query_range("application", {"query": "{}"})


class TestLokiQuery:
    @respx.mock
    def test_success(self):
        url = "http://localhost:3100/api/logs/v1/application" "/loki/api/v1/query"
        respx.get(url).mock(return_value=httpx.Response(200, json={"status": "success", "data": {}}))
        result = loki_query("application", {"query": "{}"})
        assert result["status"] == "success"


class TestLokiLabelValues:
    @respx.mock
    def test_success(self):
        url = "http://localhost:3100/api/logs/v1/application" "/loki/api/v1/label/namespace/values"
        respx.get(url).mock(return_value=httpx.Response(200, json={"status": "success", "data": ["ns1"]}))
        result = loki_label_values("application", "namespace", {"start": 0, "end": 1})
        assert result["data"] == ["ns1"]

    @respx.mock
    def test_tenant_url_routing(self):
        url = "http://localhost:3100/api/logs/v1/infrastructure" "/loki/api/v1/label/namespace/values"
        respx.get(url).mock(return_value=httpx.Response(200, json={"status": "success", "data": []}))
        result = loki_label_values("infrastructure", "namespace", {"start": 0, "end": 1})
        assert result["status"] == "success"


class TestGetLabelValues:
    @respx.mock
    def test_returns_values(self):
        url = "http://localhost:3100/api/logs/v1/application" "/loki/api/v1/label/kubernetes_namespace_name/values"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "data": ["ns1", "ns2"]},
            )
        )
        result = get_label_values("application", "kubernetes_namespace_name")
        assert result == ["ns1", "ns2"]

    @respx.mock
    def test_empty_data(self):
        url = "http://localhost:3100/api/logs/v1/application" "/loki/api/v1/label/pod/values"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json={"status": "success"},
            )
        )
        result = get_label_values("application", "pod")
        assert result == []
