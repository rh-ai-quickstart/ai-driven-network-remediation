"""LokiStack MCP tools for log searching."""

import re

import httpx

from . import config
from ._helpers import _build_logql, _query_logs
from .formatters import format_error
from .validators import validate_logql

__all__ = ["search_logs", "search_logs_regex", "query_logql"]


@config.mcp.tool()
def search_logs(
    namespace: str = "",
    pod: str = "",
    container: str = "",
    labels: dict[str, str] | None = None,
    text: str = "",
    tenant: str = config.LOKI_DEFAULT_TENANT,
    duration: str = "1h",
    limit: int = config.LOKI_MAX_LINES,
) -> dict:
    """
    Search logs with structured filters and optional literal text match.

    Builds a LogQL query from filters. Text is matched as a literal
    case-insensitive substring (special regex characters are escaped).

    Args:
        namespace:   OpenShift namespace
        pod:         Pod name substring filter
        container:   Container name filter
        labels:      Additional label matchers as {key: value}
        text:        Case-insensitive literal text search
        tenant:      LokiStack tenant: application|infrastructure|audit
        duration:    Look-back window (e.g., "1h", "30m", "7d"). Max: 24h
        limit:       Max log lines to return (default: 100, max: 500)

    Returns:
        Dict with query, tenant, duration, count, and logs list
    """
    try:
        query = _build_logql(namespace, pod, container, labels)
        if text:
            escaped_text = re.escape(text).replace('"', '\\"')
            query += f' |~ "(?i){escaped_text}"'

        return _query_logs(query, tenant, duration, limit)

    except (ValueError, TypeError, KeyError, re.error, httpx.HTTPStatusError, httpx.HTTPError) as e:
        return format_error(e)


@config.mcp.tool()
def search_logs_regex(
    namespace: str = "",
    pod: str = "",
    container: str = "",
    labels: dict[str, str] | None = None,
    regex: str = "",
    tenant: str = config.LOKI_DEFAULT_TENANT,
    duration: str = "1h",
    limit: int = config.LOKI_MAX_LINES,
) -> dict:
    """
    Search logs with structured filters and a regex line filter.

    The regex is passed directly to LogQL |~ without modification.
    Use this when you need pattern matching (e.g., "timeout|refused").

    Args:
        namespace:   OpenShift namespace
        pod:         Pod name substring filter
        container:   Container name filter
        labels:      Additional label matchers as {key: value}
        regex:       Regex line filter (passed to LogQL |~ as-is)
        tenant:      LokiStack tenant: application|infrastructure|audit
        duration:    Look-back window (e.g., "1h", "30m", "7d"). Max: 24h
        limit:       Max log lines to return (default: 100, max: 500)

    Returns:
        Dict with query, tenant, duration, count, and logs list
    """
    try:
        query = _build_logql(namespace, pod, container, labels)
        if regex:
            escaped_regex = regex.replace('"', '\\"')
            query += f' |~ "{escaped_regex}"'

        return _query_logs(query, tenant, duration, limit)

    except (ValueError, TypeError, KeyError, re.error, httpx.HTTPStatusError, httpx.HTTPError) as e:
        return format_error(e)


@config.mcp.tool()
def query_logql(
    logql_query: str,
    tenant: str = config.LOKI_DEFAULT_TENANT,
    duration: str = "1h",
    limit: int = config.LOKI_MAX_LINES,
) -> dict:
    """
    Run a raw LogQL query against LokiStack.

    Use this when you need full control over the LogQL query.
    The query must include a stream selector in curly braces.

    Args:
        logql_query: Raw LogQL query (e.g., '{namespace="my-ns"} |= "error"')
        tenant:      LokiStack tenant: application|infrastructure|audit
        duration:    Look-back window (e.g., "1h", "30m", "7d"). Max: 24h
        limit:       Max log lines to return (default: 100, max: 500)

    Returns:
        Dict with query, tenant, duration, count, and logs list
    """
    try:
        validate_logql(logql_query)
        return _query_logs(logql_query, tenant, duration, limit)

    except (ValueError, TypeError, KeyError, httpx.HTTPStatusError, httpx.HTTPError) as e:
        return format_error(e)
