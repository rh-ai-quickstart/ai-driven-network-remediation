"""Shared test helpers and factories for ran-rca-service tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from ran_rca_service.models import RCAState

SAMPLE_ANOMALY = {
    "incident_id": "test-001",
    "zone": "A",
    "application": "Twitch",
    "kpi_window": [{"RSRP": -85.0, "DL_BLER": 0.1}] * 128,
    "ad_label": "anomalous",
    "ad_confidence": 0.94,
}

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

VALID_LLM_JSON = json.dumps(
    {
        "root_cause": "Severe signal degradation in zone A detected by Mantis AD with 94% confidence. Pattern consistent with antenna misalignment causing RSRP drop and increased BLER (see 3GPP TS 38.214, Section 5.1.1).",
        "recommended_fix": "Inspect antenna alignment for the affected zone per vendor O-RAN configuration guide, Section 4.3.2. Verify physical tilt and azimuth against planned values.",
    }
)


def make_llm_response(content=VALID_LLM_JSON):
    msg = MagicMock()
    msg.content = content
    msg.usage_metadata = {"input_tokens": 120, "output_tokens": 60, "total_tokens": 180}
    return msg


def make_anomaly(**overrides) -> dict:
    return {**SAMPLE_ANOMALY, **overrides}


def make_state(**overrides) -> RCAState:
    return RCAState(**{**SAMPLE_ANOMALY, **overrides})


def project_enriched(state: RCAState, result: dict) -> dict:
    return {
        "incident_id": state.incident_id,
        "zone": state.zone,
        "application": state.application,
        "kpi_window": state.kpi_window,
        "ad_label": state.ad_label,
        "ad_confidence": state.ad_confidence,
        "root_cause": result["root_cause"],
        "recommended_fix": result["recommended_fix"],
    }
