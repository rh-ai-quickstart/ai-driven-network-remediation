"""CLI entrypoint for running RAN KPI CSV data through anomaly detection locally, without Kafka."""

from __future__ import annotations

import json
from pathlib import Path

import click
from ran_anomaly_detector.detection import AnomalyDetectionService

_SAMPLE_CSV = """\
cell_id,max_capacity,lat,lon,area_type,city,band,frequency,datetime,ues_usage,rsrp,rsrq,sinr,throughput_mbps,latency_ms
42,100,33.029427,-96.697085,industrial,Plano,Band 29,700,2026-07-29T10:00:00Z,80,-95.0,-10.0,15.0,100.0,20.0
42,100,33.029427,-96.697085,industrial,Plano,Band 29,700,2026-07-29T10:05:00Z,82,-96.0,-10.5,14.5,95.0,21.0
42,100,33.029427,-96.697085,industrial,Plano,Band 29,700,2026-07-29T10:10:00Z,78,-94.0,-9.5,15.5,98.0,19.0
42,100,33.029427,-96.697085,industrial,Plano,Band 29,700,2026-07-29T10:15:00Z,10,-125.0,-15.0,-2.0,5.0,50.0
"""


def _format_output(anomalies: list[dict]) -> str:
    if not anomalies:
        return "No anomalies detected."
    return "\n".join(json.dumps(anomaly) for anomaly in anomalies)


@click.command()
@click.option(
    "--file",
    "csv_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to a CSV file of RAN KPI readings. Defaults to a built-in sample (includes a Low RSRP "
    "reading and a throughput-drop trend).",
)
def main(csv_path: Path | None) -> None:
    """Run RAN KPI CSV data through the rule-based anomaly detector, without needing Kafka running."""
    raw_csv = csv_path.read_text() if csv_path is not None else _SAMPLE_CSV

    service = AnomalyDetectionService()
    anomalies = service.process_csv(raw_csv)

    click.echo(_format_output(anomalies))
