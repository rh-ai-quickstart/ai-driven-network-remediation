"""Typed domain models for data crossing this service's boundaries.

Replacing raw dict/str "data clumps" and "primitive obsession" with these types
means a producer-side rename or omission is caught immediately (a loud
ValidationError at parse time) instead of silently propagating as None/"n/a"
deep into the chat reply, and callers get IDE autocomplete instead of
guessing string keys.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class EnrichedAnomaly(BaseModel):
    """A single record from the ran-anomalies-enriched Kafka topic.

    Mirrors contracts/ran-anomaly-enriched.schema.json, published by
    ran-rca-service after LLM root cause analysis + RAG-based fix retrieval.
    """

    cell_id: int
    band: str
    anomaly_type: str
    anomaly: str
    root_cause: str
    recommended_fix: str
    ml_root_cause_class: str = ""
    ml_confidence: float = 0.0
    ml_steer_used: bool = False


class ModelSource(StrEnum):
    """Where a chat reply came from, returned by call_model().

    A plain string subclass (not a bare str constant) so the finite, known
    states are discoverable and typo-proof, while still comparing equal to
    and JSON-serializing as their string value (e.g. ModelSource.LIVE ==
    "live"), preserving the existing /api/chat "model.source" API contract.

    HTTP errors are NOT a fixed member here: the status code is dynamic, so
    call_model() still returns an ad hoc f"http-{code}" string for those —
    see http_error().
    """

    LIVE = "live"
    DISABLED = "disabled"
    UNREACHABLE = "unreachable"
    EMPTY = "empty"

    @staticmethod
    def http_error(status_code: int) -> str:
        return f"http-{status_code}"
