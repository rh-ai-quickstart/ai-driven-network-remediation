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
    root_cause_category: str = "unknown"
    root_cause: str = ""
    recommended_fix: str = ""

    def kpi_summary(self, top_n: int = 0) -> str:
        """Per-channel min/mean/max sorted by range (most variable first). 0 = all."""
        if not self.kpi_window:
            return ""
        stats: dict[str, list[float]] = {}
        for row in self.kpi_window:
            for k, v in row.items():
                if isinstance(v, (int, float)):
                    stats.setdefault(k, []).append(float(v))
        ranked = sorted(stats.items(), key=lambda kv: max(kv[1]) - min(kv[1]), reverse=True)
        if top_n > 0:
            ranked = ranked[:top_n]
        lines = []
        for k, vals in ranked:
            mn, mx = min(vals), max(vals)
            avg = sum(vals) / len(vals)
            lines.append(f"  {k}: min={mn:.2f} mean={avg:.2f} max={mx:.2f}")
        return "\n".join(lines)
