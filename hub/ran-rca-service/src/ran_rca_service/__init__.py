"""CLI entrypoint for invoking the RAN RCA graph locally, without Kafka."""

from __future__ import annotations

import asyncio
import json

import click

from ran_rca_service.graph import build_graph

_SAMPLE_ANOMALY = {
    "cell_id": 42,
    "band": "Band 29",
    "anomaly_type": "LowRsrp",
    "anomaly": "Low RSRP: -125.0 dBm (threshold: -110 dBm) for cell 42 on Band 29",
}


@click.command()
@click.option(
    "--anomaly-json",
    default=None,
    help="JSON string representing a detected anomaly. Defaults to a built-in sample.",
)
def main(anomaly_json: str | None) -> None:
    """Run a single anomaly through the RCA graph (stub nodes) without Kafka."""
    anomaly = json.loads(anomaly_json) if anomaly_json is not None else _SAMPLE_ANOMALY

    graph = build_graph()
    result = asyncio.run(graph.ainvoke(anomaly))

    click.echo(json.dumps(dict(result), indent=2))
