"""Typed domain models for data crossing this service's boundaries."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class EnrichedAnomaly(BaseModel):
    """A single record from the ran-anomalies-enriched Kafka topic.

    Mirrors contracts/ran-anomaly-enriched.schema.json, published by
    ran-rca-service after LLM root cause analysis on ML-detected anomalies.
    """

    incident_id: str
    zone: str
    application: str
    kpi_window: list[dict]
    ad_label: str
    ad_confidence: float
    root_cause: str
    recommended_fix: str


class ModelSource(StrEnum):
    """Where a chat reply came from, returned by call_model()."""

    LIVE = "live"
    DISABLED = "disabled"
    UNREACHABLE = "unreachable"
    EMPTY = "empty"

    @staticmethod
    def http_error(status_code: int) -> str:
        return f"http-{status_code}"
