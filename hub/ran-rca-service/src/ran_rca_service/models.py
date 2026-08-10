"""Pydantic state model for the RAN RCA graph (ADR-0001)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RCAState(BaseModel):
    cell_id: int = 0
    band: str = ""
    anomaly_type: str = ""
    anomaly: str = ""
    context_snippets: list[str] = Field(default_factory=list)
    rag_query_used: str = ""
    root_cause: str = ""
    recommended_fix: str = ""
