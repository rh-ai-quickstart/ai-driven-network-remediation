"""Shared test helpers and factories for ran-rca-service tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from ran_rca_service.models import RCAState

SAMPLE_ANOMALY = {
    "cell_id": 42,
    "band": "Band 29",
    "anomaly_type": "LowRsrp",
    "anomaly": "Low RSRP: -125.0 dBm (threshold: -110 dBm) for cell 42 on Band 29",
}

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

VALID_LLM_JSON = json.dumps(
    {
        "root_cause": "Downlink interference from adjacent cell on Band 29 causing RSRP degradation below -110 dBm threshold (see 3GPP TS 38.214, Section 5.1.1).",
        "recommended_fix": "Adjust PCI and downlink power parameters for cell 42 per vendor O-RAN configuration guide, Section 4.3.2, Table 4-5.",
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
        "cell_id": state.cell_id,
        "band": state.band,
        "anomaly_type": state.anomaly_type,
        "anomaly": state.anomaly,
        "root_cause": result["root_cause"],
        "recommended_fix": result["recommended_fix"],
    }
