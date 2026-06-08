"""LokiStack MCP tools for error-pattern grouping."""

import httpx

from . import config
from .formatters import (
    format_error,
    group_error_patterns,
)
from .tools_search import search_logs_regex

__all__ = ["find_error_patterns"]


def _collect_error_patterns(
    namespace: str,
    app: str,
    regex: str,
    tenant: str,
    duration: str,
    top_n: int,
) -> dict:
    result = search_logs_regex(
        namespace=namespace,
        regex=regex or config.DEFAULT_SEVERITY_REGEX,
        tenant=tenant,
        duration=duration,
        limit=config.LOKI_MAX_LINES_CEILING,
        labels={"app": app} if app else None,
    )

    if result.get("success") is False:
        return result

    log_lines = result.get("logs", [])
    patterns = group_error_patterns(log_lines, top_n)

    return {
        "namespace": namespace,
        "app": app,
        "tenant": tenant,
        "duration": duration,
        "total_errors": len(log_lines),
        "pattern_count": len(patterns),
        "patterns": patterns,
    }


@config.mcp.tool()
def find_error_patterns(
    namespace: str = "",
    app: str = "",
    duration: str = "30m",
    top_n: int = 10,
    tenant: str = config.LOKI_DEFAULT_TENANT,
    regex: str = "",
) -> dict:
    """
    Find and group recurring error patterns in recent logs.

    Groups errors by normalized message (stripping timestamps, IDs, IPs)
    and returns the top-N most frequent patterns with counts and samples.

    Args:
        namespace: OpenShift namespace (required)
        app:       Application label filter (optional)
        duration:  Look-back window (default: 30m). Max: 24h
        top_n:     Number of top patterns to return (default: 10, max: 50)
        tenant:    LokiStack tenant (default: application)
        regex:     Additional regex filter applied alongside the error search

    Returns:
        Dict with total errors, pattern count, and patterns list
    """
    try:
        if not namespace:
            raise ValueError("namespace is required for find_error_patterns.")
        top_n = min(max(top_n, 1), 50)

        return _collect_error_patterns(namespace, app, regex, tenant, duration, top_n)

    except (ValueError, TypeError, KeyError, httpx.HTTPStatusError, httpx.HTTPError) as e:
        return format_error(e)
