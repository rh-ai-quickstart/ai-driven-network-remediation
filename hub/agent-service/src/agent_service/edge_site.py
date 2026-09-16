"""Resolve edge_site_id from Kafka alert payloads and incident state."""

from __future__ import annotations

import json
import re
from typing import Any

from agent_service.config import EDGE_NAMESPACE
from agent_service.utils import EDGE_SITE_STAMP
# Hub-spoke demo default when CLF records omit site labels (single primary edge chart).
_DEFAULT_EDGE_SITE = "edge-01"

_SITE_LABEL_KEYS = (
    "edge_site_id",
    "adnr.io/site-id",
    "adnr_io/site-id",
)


def _first_site_value(mapping: dict[str, Any] | None) -> str:
    if not isinstance(mapping, dict):
        return ""
    for key in _SITE_LABEL_KEYS:
        raw = mapping.get(key)
        if isinstance(raw, str):
            value = raw.strip()
            if value and value != "unknown":
                return value
    return ""


def extract_edge_site_id_from_alert(data: dict[str, Any]) -> str:
    """Read edge site id from canonical or ClusterLogForwarder-shaped JSON."""
    found = _first_site_value(data.get("labels"))
    if found:
        return found

    k8s = data.get("kubernetes")
    if isinstance(k8s, dict):
        found = _first_site_value(k8s.get("labels"))
        if found:
            return found

    openshift = data.get("openshift")
    if isinstance(openshift, dict):
        for key in ("edge_site_id", *_SITE_LABEL_KEYS):
            raw = openshift.get(key)
            if isinstance(raw, str) and raw.strip() and raw.strip() != "unknown":
                return raw.strip()
        found = _first_site_value(openshift.get("labels"))
        if found:
            return found

    top = data.get("edge_site_id")
    if isinstance(top, str) and top.strip() and top.strip() != "unknown":
        return top.strip()

    return ""


def edge_site_from_resource_specs(resource_specs: str) -> str:
    if not resource_specs:
        return ""
    match = re.search(rf"{re.escape(EDGE_SITE_STAMP)}\s*([^\s\n]+)", resource_specs)
    if not match:
        return ""
    value = match.group(1).strip()
    return value if value and value != "unknown" else ""


def resolve_edge_site_id(
    log_event,
    *,
    resource_specs: str = "",
    raw_event: str = "",
) -> str:
    """Site id for MCP/AAP after normalize and optional investigate evidence."""
    if log_event is None:
        return ""
    site = (getattr(log_event, "edge_site_id", None) or "").strip()
    if site and site != "unknown":
        return site

    if raw_event:
        try:
            data = json.loads(raw_event)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            extracted = extract_edge_site_id_from_alert(data)
            if extracted:
                return extracted

    stamped = edge_site_from_resource_specs(resource_specs)
    if stamped:
        return stamped

    namespace = (getattr(log_event, "namespace", None) or "").strip()
    if namespace == EDGE_NAMESPACE:
        return _DEFAULT_EDGE_SITE

    return site or "unknown"


def remediation_should_retry(error: str, edge_site_id: str, attempt_count: int, max_retries: int) -> bool:
    """Avoid relaunching AAP when routing is invalid or proxy cannot reach a spoke."""
    if attempt_count > max_retries:
        return False
    site = (edge_site_id or "").strip()
    if site in ("", "unknown"):
        return False
    lowered = (error or "").lower()
    if "/unknown/" in lowered or "no agent available" in lowered:
        return False
    return True
