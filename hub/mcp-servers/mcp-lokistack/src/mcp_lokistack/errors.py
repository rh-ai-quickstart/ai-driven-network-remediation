"""Error taxonomy, ToolError mapping, and fuzzy matching for MCP tools."""

import difflib
from typing import NoReturn

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from . import config

INVALID_PARAMETER = "INVALID_PARAMETER"
INVALID_QUERY = "INVALID_QUERY"
MISSING_FILTERS = "MISSING_FILTERS"
TIME_RANGE_TOO_LARGE = "TIME_RANGE_TOO_LARGE"
QUERY_TIMEOUT = "QUERY_TIMEOUT"
API_ERROR = "API_ERROR"
CONNECTION_ERROR = "CONNECTION_ERROR"


def _classify_value_error(msg: str) -> str:
    lower = msg.lower()
    if "at least one filter" in lower:
        return MISSING_FILTERS
    if "exceeds maximum" in lower or "time range" in lower:
        return TIME_RANGE_TOO_LARGE
    if "logql" in lower or "stream selector" in lower:
        return INVALID_QUERY
    return INVALID_PARAMETER


def format_tool_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:200] if exc.response.text else ""
        return f"[{API_ERROR}] LokiStack API error: " f"HTTP {exc.response.status_code}. {body}"
    if isinstance(exc, httpx.ConnectError):
        return (
            f"[{CONNECTION_ERROR}] Cannot reach LokiStack at "
            f"{config.LOKI_URL}. "
            "Check LOKI_URL configuration and network connectivity."
        )
    if isinstance(exc, httpx.ReadTimeout):
        return (
            f"[{QUERY_TIMEOUT}] Query timed out after "
            f"{config.LOKI_QUERY_TIMEOUT}s. "
            "Try a shorter duration or more specific filters."
        )
    if isinstance(exc, httpx.HTTPError):
        return f"[{CONNECTION_ERROR}] LokiStack connection error: {exc}"
    msg = str(exc)
    code = _classify_value_error(msg)
    return f"[{code}] {msg}"


def raise_tool_error(exc: Exception) -> NoReturn:
    raise ToolError(format_tool_error(exc)) from exc


def suggest_values(word: str, possibilities: list[str], cutoff: float = 0.6) -> list[str]:
    return difflib.get_close_matches(word, possibilities, n=3, cutoff=cutoff)


def suggest_did_you_mean(param_name: str, value: str, valid_values: list[str]) -> str | None:
    matches = suggest_values(value, valid_values)
    if matches:
        return f"Did you mean: {', '.join(matches)}?"
    return None
