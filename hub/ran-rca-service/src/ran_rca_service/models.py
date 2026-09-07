"""Pydantic state model for the RAN RCA graph (ADR-0001)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RCAState(BaseModel):
    cell_id: int = 0
    band: str = ""
    anomaly_type: str = ""
    anomaly: str = ""
    kpi_window: list[list[float]] = Field(default_factory=list)
    ml_root_cause_class: str = ""
    ml_confidence: float = 0.0
    ml_steer_used: bool = False
    context_snippets: list[str] = Field(default_factory=list)
    rag_query_used: str = ""
    root_cause: str = ""
    recommended_fix: str = ""
