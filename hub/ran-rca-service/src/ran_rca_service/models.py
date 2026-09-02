"""Pydantic state model for the RAN RCA graph (ADR-0001).

Updated for APPENG-6023: typeless ML-detected anomalies with TelecomTS identity.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RCAState(BaseModel):
    incident_id: str = ""
    zone: str = ""
    application: str = ""
    kpi_window: list[dict] = Field(default_factory=list)
    ad_label: str = ""
    ad_confidence: float = 0.0
    context_snippets: list[str] = Field(default_factory=list)
    rag_query_used: str = ""
    root_cause: str = ""
    recommended_fix: str = ""
