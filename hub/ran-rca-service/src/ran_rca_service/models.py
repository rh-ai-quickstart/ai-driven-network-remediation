"""Pydantic state model for the RAN RCA graph (ADR-0001).

Updated for APPENG-6023: typeless ML-detected anomalies with TelecomTS identity.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator

KPI_CHANNELS = (
    "RSRP",
    "DL_BLER",
    "DL_MCS",
    "UL_BLER",
    "UL_MCS",
    "UL_NPRB",
    "UL_SNR",
    "TX_Bytes",
    "RX_Bytes",
    "Estimated_UL_Buffer",
    "PRBs_DL_Current",
    "PRBs_UL_Current",
    "PRB_Utilization_DL",
    "PRB_Utilization_UL",
    "UL_Protocol",
    "UL_NumberOfPackets",
    "DL_Protocol",
    "DL_NumberOfPackets",
)


class AnomalyInput(BaseModel):
    """Validate the Kafka anomaly contract without changing model features."""

    incident_id: str
    zone: str
    application: str
    kpi_window: list[dict]
    ad_label: str
    ad_confidence: float

    @field_validator("incident_id", "zone", "application")
    @classmethod
    def nonempty_identity(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("identity and context must be nonempty strings")
        return value

    @field_validator("ad_label")
    @classmethod
    def anomalous_label(cls, value: str) -> str:
        if value != "anomalous":
            raise ValueError("ad_label must be anomalous")
        return value

    @field_validator("ad_confidence", mode="before")
    @classmethod
    def finite_confidence(cls, value: object) -> object:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError("ad_confidence must be finite in [0, 1]")
        return value

    @field_validator("kpi_window")
    @classmethod
    def complete_window(cls, window: list[dict]) -> list[dict]:
        if len(window) != 128:
            raise ValueError("kpi_window must contain exactly 128 timesteps")
        for step, row in enumerate(window):
            for channel in KPI_CHANNELS:
                if channel not in row:
                    raise ValueError(f"missing KPI {channel!r} at timestep {step}")
                value = row[channel]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"invalid KPI {channel!r} at timestep {step}")
        return window


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
    ml_class: str = ""
    ml_class_index: int | None = None
    ml_confidence: float | None = None
    ml_status: str = "disabled"
    ml_error_reason: str = ""
    llm_error_reason: str = ""
    ml_steer_used: bool = False

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
