"""Shared test helpers for ran-anomaly-detector tests."""

from __future__ import annotations

import json

KPI_CHANNELS = [
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
]

SYNTHETIC_FIXTURES: dict[str, dict] = {
    "antenna_failure": {
        "scenario": "antenna_failure",
        "zone": "A",
        "application": "Twitch",
        "anomaly_class": "Antenna Failure",
    },
    "normal_traffic": {
        "scenario": "normal_traffic",
        "zone": "B",
        "application": "YouTube",
        "anomaly_class": "normal",
    },
    "co_channel_interference_severe": {
        "scenario": "co_channel_interference_severe",
        "zone": "A",
        "application": "Netflix",
        "anomaly_class": "Co-Channel Interference (Severe)",
    },
    "doppler_shift_severe": {
        "scenario": "doppler_shift_severe",
        "zone": "C",
        "application": "Zoom",
        "anomaly_class": "Doppler Shift (Severe)",
    },
}


def make_kpi_window(timesteps: int = 128) -> list[dict]:
    return [{ch: 0.0 for ch in KPI_CHANNELS} for _ in range(timesteps)]


def synthetic_fixture(name: str) -> dict:
    meta = SYNTHETIC_FIXTURES[name]
    return {**meta, "kpi_window": make_kpi_window()}


def synthetic_fixture_message(name: str, incident_id: str = "test-001") -> bytes:
    fixture = synthetic_fixture(name)
    msg = {
        "incident_id": incident_id,
        "zone": fixture["zone"],
        "application": fixture["application"],
        "kpi_window": fixture["kpi_window"],
    }
    return json.dumps(msg).encode("utf-8")
