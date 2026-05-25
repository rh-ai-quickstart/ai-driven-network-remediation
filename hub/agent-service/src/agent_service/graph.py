from pathlib import Path
from typing import Optional

from langgraph.graph import END, START, StateGraph

from agent_service.models import GraphConfig, RemediationState
from agent_service.nodes import (
    analyze_node,
    context_node,
    escalate_node,
    execute_node,
    ingest_node,
    notify_node,
    request_approval_node,
)
from agent_service.nodes.decide import make_decide_node


def _route_after_decide(state: RemediationState) -> str:
    return state.decision


def build_graph(config: Optional[GraphConfig] = None):
    if config is None:
        config = GraphConfig()

    graph = StateGraph(RemediationState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("context", context_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("decide", make_decide_node(config))
    graph.add_node("execute", execute_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("request_approval", request_approval_node)
    graph.add_node("notify", notify_node)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "context")
    graph.add_edge("context", "analyze")
    graph.add_edge("analyze", "decide")
    graph.add_conditional_edges(
        "decide",
        _route_after_decide,
        {"execute": "execute", "escalate": "escalate", "request_approval": "request_approval"},
    )
    graph.add_edge("execute", "notify")
    graph.add_edge("escalate", "notify")
    graph.add_edge("request_approval", "notify")
    graph.add_edge("notify", END)

    return graph.compile()


def draw_graph(output: Path, config: Optional[GraphConfig] = None) -> None:
    compiled = build_graph(config)
    png_bytes = compiled.get_graph().draw_mermaid_png()
    output.write_bytes(png_bytes)

