import asyncio
import json
from pathlib import Path

import click

from agent_service.graph import build_graph, draw_graph

_DEFAULT_RAW_EVENT = json.dumps(
    {
        "@timestamp": "2024-01-15T10:30:00Z",
        "message": "nginx CrashLoopBackOff in namespace prod",
        "level": "error",
        "kubernetes": {
            "namespace_name": "prod",
            "pod_name": "nginx-abc123",
            "container_name": "nginx",
        },
        "labels": {
            "edge_site_id": "edge-site-01",
        },
    }
)


def _format_result(result: dict) -> str:
    rca = result.get("root_cause_analysis")
    remediation = result.get("remediation_result")

    lines = [
        f"incident_id: {result.get('incident_id', '')}",
        f"next_action: {result.get('decision', '')}",
    ]

    if rca:
        lines.append(f"rca: {rca.failure_type} (confidence={rca.confidence}, severity={rca.estimated_severity})")
        lines.append(f"  summary: {rca.summary}")

    if remediation:
        lines.append(f"remediation: {remediation.action_taken} (success={remediation.success})")
        if remediation.generated_playbook_name:
            lines.append(f"  playbook: {remediation.generated_playbook_name}")

    return "\n".join(lines)


@click.command()
@click.option(
    "--draw", "draw_path", type=click.Path(path_type=Path), default=None, help="Draw the graph to a PNG file and exit."
)
def main(draw_path: Path | None) -> None:
    if draw_path is not None:
        draw_graph(draw_path)
        click.echo(f"Graph saved to {draw_path}")
        return
    graph = build_graph()
    result = asyncio.run(graph.ainvoke({"raw_event": _DEFAULT_RAW_EVENT}))
    click.echo(_format_result(result))
