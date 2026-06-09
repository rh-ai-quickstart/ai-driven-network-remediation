import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp_lokistack.errors import (
    format_tool_error,
    raise_tool_error,
    suggest_did_you_mean,
    suggest_values,
)


class TestFormatToolError:
    def test_value_error(self):
        msg = format_tool_error(ValueError("bad input"))
        assert msg.startswith("[INVALID_PARAMETER]")
        assert "bad input" in msg

    def test_missing_filters(self):
        msg = format_tool_error(ValueError("At least one filter is required"))
        assert msg.startswith("[MISSING_FILTERS]")

    def test_time_range_too_large(self):
        msg = format_tool_error(ValueError("Duration exceeds maximum allowed"))
        assert msg.startswith("[TIME_RANGE_TOO_LARGE]")

    def test_logql_invalid_query(self):
        msg = format_tool_error(ValueError("LogQL query must include a stream selector"))
        assert msg.startswith("[INVALID_QUERY]")

    def test_http_status_error(self):
        resp = httpx.Response(
            status_code=429,
            request=httpx.Request("GET", "http://test"),
        )
        exc = httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        msg = format_tool_error(exc)
        assert msg.startswith("[API_ERROR]")
        assert "429" in msg

    def test_connect_error(self):
        exc = httpx.ConnectError("connection refused")
        msg = format_tool_error(exc)
        assert msg.startswith("[CONNECTION_ERROR]")
        assert "Cannot reach" in msg

    def test_read_timeout(self):
        exc = httpx.ReadTimeout("timeout")
        msg = format_tool_error(exc)
        assert msg.startswith("[QUERY_TIMEOUT]")
        assert "timed out" in msg

    def test_generic_http_error(self):
        exc = httpx.DecodingError("bad encoding")
        msg = format_tool_error(exc)
        assert msg.startswith("[CONNECTION_ERROR]")

    def test_unknown_exception(self):
        msg = format_tool_error(RuntimeError("unexpected"))
        assert "[INVALID_PARAMETER]" in msg
        assert "unexpected" in msg


class TestRaiseToolError:
    def test_raises_tool_error(self):
        with pytest.raises(ToolError, match="INVALID_PARAMETER.*bad input"):
            raise_tool_error(ValueError("bad input"))

    def test_chains_original_exception(self):
        original = ValueError("original")
        with pytest.raises(ToolError) as exc_info:
            raise_tool_error(original)
        assert exc_info.value.__cause__ is original


class TestSuggestValues:
    def test_close_match(self):
        result = suggest_values("audit", ["application", "infrastructure", "audit"])
        assert "audit" in result

    def test_close_fuzzy_match(self):
        result = suggest_values("admin", ["application", "infrastructure", "audit"])
        assert "audit" in result

    def test_no_match(self):
        result = suggest_values("xyz", ["application", "infrastructure", "audit"])
        assert result == []

    def test_cutoff(self):
        result = suggest_values("infra", ["infrastructure"], cutoff=0.3)
        assert len(result) == 1
        result_strict = suggest_values("infra", ["infrastructure"], cutoff=0.9)
        assert result_strict == []


class TestSuggestDidYouMean:
    def test_with_matches(self):
        result = suggest_did_you_mean("tenant", "admin", ["application", "infrastructure", "audit"])
        assert result is not None
        assert "Did you mean" in result
        assert "audit" in result

    def test_no_matches(self):
        result = suggest_did_you_mean("tenant", "xyz", ["application", "infrastructure", "audit"])
        assert result is None
