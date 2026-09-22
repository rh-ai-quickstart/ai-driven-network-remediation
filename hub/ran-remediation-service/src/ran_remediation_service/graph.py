"""LangGraph pipeline for RAN remediation: decide → remediate → notify → audit.

Mirrors the Workflow 1 graph structure (agent-service/graph.py) — a linear
StateGraph with typed Pydantic state. No branching needed here since every
detected anomaly is remediated automatically.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ran_remediation_service.models import RemediationState
from ran_remediation_service.nodes import audit_node, decide_node, notify_node, remediate_node


def build_graph():
    graph = StateGraph(RemediationState)

    graph.add_node("decide", decide_node)
    graph.add_node("remediate", remediate_node)
    graph.add_node("notify", notify_node)
    graph.add_node("audit", audit_node)

    graph.add_edge(START, "decide")
    graph.add_edge("decide", "remediate")
    graph.add_edge("remediate", "notify")
    graph.add_edge("notify", "audit")
    graph.add_edge("audit", END)

    return graph.compile()
