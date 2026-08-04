"""Shared test helpers and factories for ran-rca-service tests."""

from __future__ import annotations

from ran_rca_service.models import RCAState

SAMPLE_ANOMALY = {
    "cell_id": 42,
    "band": "Band 29",
    "anomaly_type": "LowRsrp",
    "anomaly": "Low RSRP: -125.0 dBm (threshold: -110 dBm) for cell 42 on Band 29",
}


def make_anomaly(**overrides) -> dict:
    return {**SAMPLE_ANOMALY, **overrides}


def make_state(**overrides) -> RCAState:
    return RCAState(**{**SAMPLE_ANOMALY, **overrides})
