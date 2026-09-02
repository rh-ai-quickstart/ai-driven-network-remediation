"""TelecomTS fixture catalog for demo injection.

Provides checked-in TelecomTS lab-trace samples for reproducible demos.
Each fixture is a JSON file with a known anomaly class (or normal) that
the Mantis AD model has been validated against.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"

KPI_CHANNELS = [
    "RSRP", "DL_BLER", "DL_MCS", "UL_BLER", "UL_MCS", "UL_NPRB",
    "UL_SNR", "TX_Bytes", "RX_Bytes", "Estimated_UL_Buffer",
    "PRBs_DL_Current", "PRBs_UL_Current", "PRB_Utilization_DL",
    "PRB_Utilization_UL", "UL_Protocol", "UL_NumberOfPackets",
    "DL_Protocol", "DL_NumberOfPackets",
]

DEFAULT_SCENARIO = "antenna_failure"


def list_scenarios() -> list[str]:
    """Return all available scenario IDs from the fixture catalog."""
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def get_fixture(scenario: str) -> dict[str, Any]:
    """Load a fixture by scenario ID. Falls back to DEFAULT_SCENARIO for unknown IDs."""
    normalized = scenario.strip().lower().replace("-", "_").replace(" ", "_")
    path = FIXTURES_DIR / f"{normalized}.json"
    if not path.exists():
        path = FIXTURES_DIR / f"{DEFAULT_SCENARIO}.json"
    return json.loads(path.read_text())


def fixture_to_kpi_message(fixture: dict[str, Any], incident_id: str) -> dict[str, Any]:
    """Build a Kafka metrics message from a fixture for demo injection.

    This is the JSON payload published to ran-combined-metrics by the demo trigger.
    The detector deserializes it, extracts kpi_window, and POSTs to the predictor.
    """
    return {
        "incident_id": incident_id,
        "zone": fixture["zone"],
        "application": fixture["application"],
        "kpi_window": fixture["kpi_window"],
    }
