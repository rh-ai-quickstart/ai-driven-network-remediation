"""Shared private helpers for LokiStack MCP tools."""

import re
from datetime import datetime, timezone

from . import config
from .client import loki_query_range
from .formatters import format_log_streams
from .validators import (
    _duration_to_seconds,
    validate_duration,
    validate_limit,
    validate_namespace,
    validate_tenant,
)

_INVALID_LABEL_KEY_RE = re.compile(r'["\s={}]')


def _escape_logql_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _validate_label_key(key: str) -> None:
    if not key or _INVALID_LABEL_KEY_RE.search(key):
        raise ValueError(
            f"Invalid label key '{key}'. " "Label keys must not contain quotes, spaces, " "equals signs, or braces."
        )


def _time_range_ns(duration: str) -> tuple[int, int]:
    end_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    start_ns = end_ns - _duration_to_seconds(duration) * 1_000_000_000
    return start_ns, end_ns


def _build_logql(
    namespace: str,
    pod: str,
    container: str,
    labels: dict[str, str] | None,
) -> str:
    selectors = []
    if namespace:
        validate_namespace(namespace)
        selectors.append(f'kubernetes_namespace_name="{namespace}"')
    if pod:
        escaped = re.escape(pod)
        selectors.append(f'kubernetes_pod_name=~".*{escaped}.*"')
    if container:
        selectors.append(f'kubernetes_container_name="{_escape_logql_string(container)}"')
    if labels:
        for k, v in labels.items():
            _validate_label_key(k)
            selectors.append(f'{k}="{_escape_logql_string(v)}"')

    if not selectors:
        raise ValueError(
            "At least one filter (namespace, pod, container, labels) "
            "is required. "
            "Example: namespace='my-namespace'"
        )

    return "{" + ", ".join(selectors) + "}"


def _build_metric_selector(namespace: str, app: str) -> str:
    parts = []
    if namespace:
        parts.append(f'kubernetes_namespace_name="{_escape_logql_string(namespace)}"')
    if app:
        parts.append(f'app="{_escape_logql_string(app)}"')
    return "{" + ", ".join(parts) + "}"


def _build_metric_logql(metric_type: str, selector: str, step: str) -> str:
    line_filter = f' |~ "{config.DEFAULT_SEVERITY_REGEX}"' if metric_type == "error_rate" else ""
    return f"sum by (kubernetes_namespace_name) " f"(count_over_time({selector}{line_filter} [{step}]))"


def _query_logs(
    query: str,
    tenant: str,
    duration: str,
    limit: int,
    corrections: list[str] | None = None,
) -> dict:
    tenant, tenant_note = validate_tenant(tenant)
    validate_duration(duration)
    limit = validate_limit(limit)

    all_corrections = list(corrections) if corrections else []
    if tenant_note:
        all_corrections.append(tenant_note)

    start_ns, end_ns = _time_range_ns(duration)

    data = loki_query_range(
        tenant,
        {
            "query": query,
            "start": start_ns,
            "end": end_ns,
            "limit": limit,
            "direction": "backward",
        },
    )

    log_lines = format_log_streams(data, limit)
    count = len(log_lines)

    result = {
        "query": query,
        "tenant": tenant,
        "duration": duration,
        "count": count,
        "logs": log_lines,
    }

    if all_corrections:
        result["corrections"] = all_corrections

    if count == 0:
        result["hints"] = [
            "No logs matched. The service may be healthy " "with no errors in this period.",
            "Verify the namespace and tenant are correct.",
            f"Try a longer duration (current: {duration}).",
        ]

    return result
