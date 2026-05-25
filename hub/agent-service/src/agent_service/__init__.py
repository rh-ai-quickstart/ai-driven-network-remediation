from pathlib import Path

import click

from agent_service.graph import build_graph, draw_graph


@click.command()
@click.option("--confidence", type=float, default=0.85, help="Override confidence for smoke testing.")
@click.option(
    "--draw", "draw_path", type=click.Path(path_type=Path), default=None, help="Draw the graph to a PNG file and exit."
)
def main(confidence: float, draw_path: Path | None) -> None:
    if draw_path is not None:
        draw_graph(draw_path)
        click.echo(f"Graph saved to {draw_path}")
        return
    graph = build_graph()
    result = graph.invoke({"raw_event": "nginx CrashLoopBackOff in namespace prod", "confidence_override": confidence})
    print(result)
