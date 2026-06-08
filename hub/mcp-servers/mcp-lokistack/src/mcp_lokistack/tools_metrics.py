"""LokiStack MCP tools for log-based metrics."""

import httpx

from . import config
from ._helpers import (
    _build_metric_logql,
    _build_metric_selector,
    _time_range_ns,
)
from .client import loki_query_range
from .formatters import format_error, format_metric_series
from .validators import (
    _duration_to_seconds,
    validate_duration,
    validate_metric_type,
    validate_namespace,
    validate_step,
    validate_tenant,
)

__all__ = ["query_metrics"]


@config.mcp.tool()
def query_metrics(
    metric_type: str,
    namespace: str = "",
    app: str = "",
    duration: str = "1h",
    step: str = "5m",
    tenant: str = config.LOKI_DEFAULT_TENANT,
) -> dict:
    """
    Query log-based metrics from LokiStack for trend analysis.

    Args:
        metric_type: One of: error_rate, log_volume
        namespace:   OpenShift namespace
        app:         Application label filter (optional)
        duration:    Time window (e.g., "1h", "6h", "24h"). Max: 24h
        step:        Bucket size for time series (e.g., "5m", "1h")
        tenant:      LokiStack tenant (default: application)

    Returns:
        Dict with metric_type, data points, total, and average
    """
    try:
        validate_tenant(tenant)
        validate_duration(duration)
        validate_metric_type(metric_type)
        validate_step(step, duration)
        if namespace:
            validate_namespace(namespace)

        selector = _build_metric_selector(namespace, app)
        logql = _build_metric_logql(metric_type, selector, step)
        start_ns, end_ns = _time_range_ns(duration)

        data = loki_query_range(
            tenant,
            {
                "query": logql,
                "start": start_ns,
                "end": end_ns,
                "step": str(_duration_to_seconds(step)),
            },
        )

        data_points = format_metric_series(data)
        total = sum(dp["value"] for dp in data_points)

        return {
            "metric_type": metric_type,
            "namespace": namespace,
            "app": app,
            "tenant": tenant,
            "duration": duration,
            "step": step,
            "total": total,
            "average_per_step": round(total / max(len(data_points), 1), 1),
            "data_points": data_points,
        }

    except (ValueError, TypeError, KeyError, httpx.HTTPStatusError, httpx.HTTPError) as e:
        return format_error(e)
