"""Pydantic state model for the RAN remediation LangGraph pipeline."""

from __future__ import annotations

import time

from pydantic import BaseModel, Field


class RemediationState(BaseModel):
    # ── Input (from ran-anomalies-enriched) ──────────────────────
    incident_id: str = ""
    zone: str = ""
    application: str = ""
    ad_label: str = ""
    ad_confidence: float = 0.0
    root_cause: str = ""
    recommended_fix: str = ""

    # ── Set by decide node ────────────────────────────────────────
    template_name: str = ""
    extra_vars: dict = Field(default_factory=dict)

    # ── Set by remediate node ─────────────────────────────────────
    job_id: str = ""
    job_status: str = ""
    success: bool = False
    timed_out: bool = False
    output_summary: str = ""
    timestamp: str = ""

    # ── Set by audit node ─────────────────────────────────────────
    kafka_offset: int = -1

    # ── Internal timing (same pattern as Workflow 1 IncidentState) ─
    incident_start_ms: float = Field(default_factory=lambda: time.time() * 1000)
    total_duration_ms: float = 0.0
