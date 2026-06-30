"""SLO metrics, incident normalization, and incident movie builder."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from .config import (
    AUDIT_LOOKBACK_HOURS,
    BASELINE_MANUAL_MTTR_SECONDS,
    OPS_HOURLY_COST_USD,
)
from .utils import parse_iso


def normalize_incident_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Kafka audit records into a consistent shape."""
    event = payload
    if isinstance(payload.get("event"), dict):
        event = payload["event"]
    elif isinstance(payload.get("payload"), dict):
        event = payload["payload"]

    labels = event.get("labels") if isinstance(event.get("labels"), dict) else {}
    message = str(event.get("message", "") or payload.get("message", "") or "")
    scenario = str(event.get("failure_type") or labels.get("dark_noc_scenario") or "unknown")
    remediation_action = str(event.get("remediation_action") or payload.get("remediation_action") or "detected")
    remediation_success = bool(event.get("remediation_success", payload.get("remediation_success", False)))
    servicenow_ticket = str(event.get("servicenow_ticket") or payload.get("servicenow_ticket") or "")
    aap_job_id = str(event.get("aap_job_id") or payload.get("aap_job_id") or "")
    confidence = float(event.get("ai_confidence") or payload.get("ai_confidence") or 0)
    duration_ms = float(event.get("total_duration_ms") or payload.get("total_duration_ms") or 0)
    edge_site = str(event.get("edge_site_id") or payload.get("edge_site_id") or labels.get("edge_site_id") or "edge-01")

    ts_raw = ""
    for key in ("timestamp", "@timestamp", "time", "ts"):
        val = event.get(key) or payload.get(key)
        if val:
            ts_raw = str(val)
            break

    incident_id = str(
        event.get("incident_id") or payload.get("incident_id") or f"evt-{abs(hash(ts_raw + message)) % 10_000_000}"
    )
    severity = str(
        event.get("severity")
        or payload.get("severity")
        or ("high" if "error" in str(event.get("level", "")).lower() else "medium")
    )

    return {
        "timestamp": ts_raw,
        "incident_id": incident_id,
        "failure_type": scenario,
        "severity": severity,
        "remediation_action": remediation_action,
        "remediation_success": remediation_success,
        "servicenow_ticket": servicenow_ticket,
        "aap_job_id": aap_job_id,
        "edge_site_id": edge_site,
        "ai_confidence": confidence,
        "total_duration_ms": duration_ms,
    }


def compute_slo_metrics(records: list[dict[str, Any]], up_count: int, total_count: int) -> dict[str, Any]:
    """Compute SLO metrics from incident audit records."""
    total = len(records)
    if total == 0:
        return {
            "window_hours": AUDIT_LOOKBACK_HOURS,
            "sample_size": 0,
            "mttd_seconds": None,
            "mttr_seconds": None,
            "p95_recovery_seconds": None,
            "auto_remediation_pct": None,
            "escalation_pct": None,
            "aap_success_pct": None,
            "ai_confidence_avg": None,
            "incidents_per_hour": 0.0,
            "platform_availability_pct": round((up_count / total_count) * 100, 2) if total_count else 0.0,
        }

    durations: list[float] = []
    auto_remediated = 0
    escalated = 0
    aap_total = 0
    aap_success = 0
    confidence_vals: list[float] = []

    for rec in records:
        dur_ms = float(rec.get("total_duration_ms", 0) or 0)
        if dur_ms > 0:
            durations.append(dur_ms / 1000.0)

        confidence = float(rec.get("ai_confidence", 0) or 0)
        if confidence > 0:
            confidence_vals.append(confidence)

        if rec.get("remediation_success") and not rec.get("servicenow_ticket"):
            auto_remediated += 1
        if rec.get("servicenow_ticket") or "escalat" in str(rec.get("remediation_action", "")).lower():
            escalated += 1
        if rec.get("aap_job_id"):
            aap_total += 1
            if rec.get("remediation_success"):
                aap_success += 1

    mttr = statistics.mean(durations) if durations else None
    # Synthetic estimate — real MTTD requires separate detection timestamps from the agent
    mttd = statistics.mean([max(1.0, d * 0.2) for d in durations]) if durations else None
    p95 = statistics.quantiles(durations, n=20)[18] if len(durations) >= 20 else (max(durations) if durations else None)

    return {
        "window_hours": AUDIT_LOOKBACK_HOURS,
        "sample_size": total,
        "mttd_seconds": round(mttd, 2) if mttd is not None else None,
        "mttr_seconds": round(mttr, 2) if mttr is not None else None,
        "p95_recovery_seconds": round(p95, 2) if p95 is not None else None,
        "auto_remediation_pct": round((auto_remediated / total) * 100, 2),
        "escalation_pct": round((escalated / total) * 100, 2),
        "aap_success_pct": round((aap_success / aap_total) * 100, 2) if aap_total else None,
        "ai_confidence_avg": round(statistics.mean(confidence_vals), 3) if confidence_vals else None,
        "incidents_per_hour": round(total / max(1, AUDIT_LOOKBACK_HOURS), 2),
        "platform_availability_pct": round((up_count / total_count) * 100, 2) if total_count else 0.0,
    }


def build_incident_movie(
    records: list[dict[str, Any]], slo: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build incident timeline and business impact from audit records."""
    ordered = sorted(
        records,
        key=lambda r: parse_iso(str(r.get("timestamp", ""))) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    movie: list[dict[str, Any]] = []
    auto_resolved = 0
    escalated = 0
    success_count = 0
    confidence_vals: list[float] = []

    for rec in ordered[:8]:
        success = bool(rec.get("remediation_success", False))
        servicenow_ticket = str(rec.get("servicenow_ticket", "") or "")
        action = str(rec.get("remediation_action", "n/a") or "n/a")
        ts = parse_iso(str(rec.get("timestamp", "")))

        if success:
            success_count += 1
        if success and not servicenow_ticket:
            auto_resolved += 1
        if servicenow_ticket:
            escalated += 1

        confidence = float(rec.get("ai_confidence", 0) or 0)
        if confidence > 0:
            confidence_vals.append(confidence)

        if not success and not servicenow_ticket and action.lower() in {"detected", "n/a", "none", ""}:
            stage = "Detected"
        elif success and not servicenow_ticket:
            stage = "Auto-Remediated"
        elif success:
            stage = "Remediated"
        else:
            stage = "Escalated"

        movie.append(
            {
                "timestamp": ts.isoformat() if ts else str(rec.get("timestamp", "")),
                "incident_id": str(rec.get("incident_id", "n/a")),
                "title": f"{rec.get('failure_type', 'unknown')} on {rec.get('edge_site_id', 'edge-01')}",
                "stage": stage,
                "summary": f"Action: {action} · Result: {'success' if success else 'failed'}",
                "artifacts": {
                    "aap_job_id": rec.get("aap_job_id") or None,
                    "servicenow_ticket": servicenow_ticket or None,
                },
            }
        )

    total = len(records)
    mttr = float(slo.get("mttr_seconds") or 0)
    # Placeholder: replace with real org data when available (configurable via env vars)
    per_incident_saved = max(0.0, BASELINE_MANUAL_MTTR_SECONDS - mttr)
    total_seconds_saved = per_incident_saved * auto_resolved

    impact = {
        "incidents_processed": total,
        "remediation_success_pct": round((success_count / total) * 100, 2) if total else 0.0,
        "tickets_avoided": auto_resolved,
        "escalated_tickets": escalated,
        "hours_returned_to_ops": round(total_seconds_saved / 3600.0, 2),
        "estimated_cost_saved_usd": round((total_seconds_saved / 3600.0) * OPS_HOURLY_COST_USD, 2),
        "model_confidence_avg": round(statistics.mean(confidence_vals), 3) if confidence_vals else None,
    }
    return movie, impact
