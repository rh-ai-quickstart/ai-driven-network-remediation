#!/usr/bin/env python3
"""Generate TelecomTS fixture samples from HuggingFace for the demo catalog.

Run once locally to produce checked-in JSON fixtures:
    python scripts/generate_fixtures.py

Requires: pip install datasets
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from datasets import load_dataset

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "hub" / "telco-oran" / "src" / "telco_oran" / "fixtures"
SEED = 42

KPI_CHANNELS = [
    "RSRP", "DL_BLER", "DL_MCS", "UL_BLER", "UL_MCS", "UL_NPRB",
    "UL_SNR", "TX_Bytes", "RX_Bytes", "Estimated_UL_Buffer",
    "PRBs_DL_Current", "PRBs_UL_Current", "PRB_Utilization_DL",
    "PRB_Utilization_UL", "UL_Protocol", "UL_NumberOfPackets",
    "DL_Protocol", "DL_NumberOfPackets",
]

PROTOCOL_MAP = {"TCP": 0, "UDP": 1, None: 2, "None": 2}

WANTED_SCENARIOS = {
    "Antenna Failure": "antenna_failure",
    "High Network Congestion (Sudden Spike)": "high_congestion_sudden",
    "Co-Channel Interference (Severe)": "co_channel_interference_severe",
    "Doppler Shift (Severe)": "doppler_shift_severe",
}


def encode_protocol(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    return PROTOCOL_MAP.get(value, 2)


def sample_to_fixture(sample: dict, scenario_id: str) -> dict:
    """Convert a HuggingFace TelecomTS sample to our fixture format."""
    kpis = sample["KPIs"]
    labels = sample.get("labels", {})
    anomalies = sample.get("anomalies", {})

    kpi_window = []
    for t in range(128):
        timestep = {}
        for ch in KPI_CHANNELS:
            values = kpis[ch]
            val = values[t]
            if ch in ("UL_Protocol", "DL_Protocol"):
                val = encode_protocol(val)
            timestep[ch] = val
        kpi_window.append(timestep)

    return {
        "scenario": scenario_id,
        "zone": labels.get("zone", "A"),
        "application": labels.get("application", "Unknown"),
        "anomaly_class": anomalies.get("type", "normal"),
        "kpi_window": kpi_window,
    }


def main():
    print("Loading TelecomTS dataset from HuggingFace...")
    dataset = load_dataset(
        "AliMaatouk/TelecomTS",
        data_files={"full": "**/chunked.jsonl"},
    )["full"]

    random.seed(SEED)
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    found = {}
    normal_candidates = []

    for sample in dataset:
        anomalies = sample.get("anomalies", {})
        atype = anomalies.get("type")

        if not anomalies.get("exists"):
            if len(normal_candidates) < 10:
                normal_candidates.append(sample)
            continue

        if atype in WANTED_SCENARIOS and atype not in found:
            found[atype] = sample
            if len(found) == len(WANTED_SCENARIOS):
                break

    for atype, scenario_id in WANTED_SCENARIOS.items():
        if atype not in found:
            print(f"  WARNING: Could not find sample for '{atype}'")
            continue
        fixture = sample_to_fixture(found[atype], scenario_id)
        path = FIXTURES_DIR / f"{scenario_id}.json"
        path.write_text(json.dumps(fixture, indent=2))
        print(f"  Written: {path.name} (zone={fixture['zone']}, app={fixture['application']})")

    if normal_candidates:
        normal = random.choice(normal_candidates)
        fixture = sample_to_fixture(normal, "normal_traffic")
        fixture["anomaly_class"] = "normal"
        path = FIXTURES_DIR / "normal_traffic.json"
        path.write_text(json.dumps(fixture, indent=2))
        print(f"  Written: {path.name} (zone={fixture['zone']}, app={fixture['application']})")

    print(f"\nDone. {len(list(FIXTURES_DIR.glob('*.json')))} fixtures in {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
