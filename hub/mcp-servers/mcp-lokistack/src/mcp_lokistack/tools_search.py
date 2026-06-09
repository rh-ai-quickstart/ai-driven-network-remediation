"""LokiStack MCP tools for log searching."""

import logging
import re

import httpx

from . import config
from ._helpers import _build_logql, _query_logs
from .client import get_label_values
from .errors import raise_tool_error, suggest_did_you_mean
from .validators import validate_logql

__all__ = ["search_logs", "search_logs_regex", "query_logql"]

_log = logging.getLogger(__name__)
_BARE_LABEL_CHECKS = [
    (
        re.compile(r"[{,]\s*namespace\s*[=!~]"),
        "kubernetes_namespace_name",
        "namespace",
    ),
    (
        re.compile(r"[{,]\s*pod\s*[=!~]"),
        "kubernetes_pod_name",
        "pod",
    ),
    (
        re.compile(r"[{,]\s*container\s*[=!~]"),
        "kubernetes_container_name",
        "container",
    ),
]

_LABEL_MAP = {bare: full for _, full, bare in _BARE_LABEL_CHECKS}


def _enrich_empty_results(result: dict, tenant: str, **params: str) -> dict:
    if result["count"] > 0:
        return result
    hints = result.get("hints", [])
    for param_name, value in params.items():
        if not value:
            continue
        label = _LABEL_MAP.get(param_name)
        if not label:
            continue
        try:
            known = get_label_values(tenant, label)
        except Exception:
            _log.debug("Failed to fetch label values for %s", label)
            continue
        hint = suggest_did_you_mean(param_name, value, known)
        if hint:
            hints.append(f"{param_name}='{value}': {hint}")
    if hints:
        result["hints"] = hints
    return result


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

        result = _query_logs(query, tenant, duration, limit)
        return _enrich_empty_results(
            result,
            result["tenant"],
            namespace=namespace,
            pod=pod,
            container=container,
        )

    except (ValueError, TypeError, KeyError, re.error, httpx.HTTPStatusError, httpx.HTTPError) as e:
        raise_tool_error(e)


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

        result = _query_logs(query, tenant, duration, limit)
        return _enrich_empty_results(
            result,
            result["tenant"],
            namespace=namespace,
            pod=pod,
            container=container,
        )

    except (ValueError, TypeError, KeyError, re.error, httpx.HTTPStatusError, httpx.HTTPError) as e:
        raise_tool_error(e)


@config.mcp.tool()
def query_logql(
    logql_query: str,
    tenant: str = config.LOKI_DEFAULT_TENANT,
    duration: str = "1h",
    limit: int = config.LOKI_MAX_LINES,
) -> dict:
    """
    Run a raw LogQL query against LokiStack.

    Prefer search_logs for most queries — it builds correct LogQL for you.
    Use this only when you need full control over the query.
    OpenShift LokiStack labels use the 'kubernetes_' prefix:
    'kubernetes_namespace_name', 'kubernetes_pod_name',
    'kubernetes_container_name' — not 'namespace', 'pod', 'container'.

    Args:
        logql_query: Raw LogQL query
            (e.g., '{kubernetes_namespace_name="my-ns"} |= "error"')
        tenant:      LokiStack tenant: application|infrastructure|audit
        duration:    Look-back window (e.g., "1h", "30m", "7d"). Max: 24h
        limit:       Max log lines to return (default: 100, max: 500)

    Returns:
        Dict with query, tenant, duration, count, and logs list
    """
    try:
        validate_logql(logql_query)
        selector_start = logql_query.index("{")
        selector_end = logql_query.index("}", selector_start)
        stream_selector = logql_query[selector_start:selector_end + 1]
        for pattern, correct, bare in _BARE_LABEL_CHECKS:
            if pattern.search(stream_selector):
                raise ValueError(
                    f"LogQL uses '{correct}' not '{bare}'. "
                    f'Example: {{{correct}="value"}}'
                )
        return _query_logs(logql_query, tenant, duration, limit)

    except (ValueError, TypeError, KeyError, httpx.HTTPStatusError, httpx.HTTPError) as e:
        raise_tool_error(e)
