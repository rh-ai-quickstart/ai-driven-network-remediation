"""Input validation with AI-friendly error messages."""

import re

from .config import (
    LOKI_MAX_DURATION,
    LOKI_MAX_LINES_CEILING,
    VALID_TENANTS,
)
from .errors import suggest_values

_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")
_VALID_METRIC_TYPES = ("error_rate", "log_volume")


def _duration_to_seconds(duration: str) -> int:
    m = _DURATION_RE.match(duration)
    if not m:
        raise ValueError(
            f"Invalid duration '{duration}'. "
            "Use format like '30m', '1h', '6h', '1d' "
            "(number followed by s/m/h/d)."
        )
    return int(m.group(1)) * _DURATION_UNITS[m.group(2)]


def _validate_enum(
    param_name: str,
    value: str,
    valid_values: list[str],
    usage_hint: str,
) -> tuple[str, str | None]:
    if value in valid_values:
        return value, None
    matches = suggest_values(value, valid_values)
    if len(matches) == 1:
        note = f"{param_name}: '{value}' → '{matches[0]}'"
        return matches[0], note
    msg = (
        f"Invalid {param_name} '{value}'. Must be one of: "
        f"{', '.join(valid_values)}. {usage_hint}"
    )
    if matches:
        msg += f" Did you mean: {', '.join(matches)}?"
    raise ValueError(msg)


def validate_tenant(tenant: str) -> tuple[str, str | None]:
    return _validate_enum(
        "tenant",
        tenant,
        list(VALID_TENANTS),
        "Use 'application' for workload logs, "
        "'infrastructure' for node/system logs, "
        "'audit' for API audit logs.",
    )


def validate_duration(duration: str) -> None:
    seconds = _duration_to_seconds(duration)
    if seconds <= 0:
        raise ValueError(f"Duration '{duration}' must be greater than zero.")
    max_seconds = _duration_to_seconds(LOKI_MAX_DURATION)
    if seconds > max_seconds:
        raise ValueError(
            f"Duration '{duration}' exceeds maximum allowed " f"({LOKI_MAX_DURATION}). Use a shorter time range."
        )


def validate_limit(limit: int) -> int:
    if limit < 1:
        raise ValueError("limit must be >= 1.")
    return min(limit, LOKI_MAX_LINES_CEILING)


def validate_namespace(namespace: str) -> None:
    if not _NAMESPACE_RE.match(namespace):
        raise ValueError(
            f"Invalid namespace '{namespace}'. "
            "Must be a valid OpenShift namespace name "
            "(lowercase alphanumeric and hyphens, 1-63 chars, "
            "must start and end with alphanumeric)."
        )


_LOGQL_AGG_PREFIX = re.compile(
    r"^\s*(?:sum|count|rate|avg|min|max|topk|bottomk|"
    r"stddev|stdvar|count_over_time|bytes_over_time|"
    r"bytes_rate|first_over_time|last_over_time)\s*"
)


def validate_logql(query: str) -> None:
    stripped = query.strip()
    if not stripped:
        raise ValueError("LogQL query cannot be empty. " 'Provide a query like: {kubernetes_namespace_name="my-ns"} |= "error"')
    if len(stripped) > 2048:
        raise ValueError(
            f"LogQL query is too long ({len(stripped)} chars, max 2048). "
            "Simplify the query or use structured parameters instead."
        )
    if "{" not in stripped or "}" not in stripped:
        raise ValueError(
            "LogQL query must include a stream selector " "in curly braces. " 'Example: {kubernetes_namespace_name="my-ns"} |= "error"'
        )
    if stripped.index("{") > stripped.index("}"):
        raise ValueError(
            "Malformed LogQL: closing brace appears before " "opening brace. " 'Example: {kubernetes_namespace_name="my-ns"} |= "error"'
        )
    if not stripped.startswith("{") and not _LOGQL_AGG_PREFIX.match(stripped):
        raise ValueError(
            "LogQL query must start with a stream selector "
            "'{...}' or an aggregation function. "
            'Example: {kubernetes_namespace_name="my-ns"} |= "error"'
        )


def validate_metric_type(metric_type: str) -> tuple[str, str | None]:
    return _validate_enum(
        "metric_type",
        metric_type,
        list(_VALID_METRIC_TYPES),
        "Use 'error_rate' for error trend analysis, "
        "'log_volume' for total log throughput.",
    )


def validate_step(step: str, duration: str) -> None:
    step_s = _duration_to_seconds(step)
    duration_s = _duration_to_seconds(duration)
    if step_s > duration_s:
        raise ValueError(
            f"Step '{step}' is larger than duration '{duration}'. "
            "Step must be <= duration for meaningful time series data."
        )
