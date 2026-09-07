"""LangGraph RCA pipeline: START -> classify -> rag_retrieval -> analyze -> END."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from ran_rca_service.models import RCAState
from ran_rca_service.nodes.analyze import analyze_node
from ran_rca_service.nodes.classify import classify_node
from ran_rca_service.nodes.rag_retrieval import rag_retrieval_node


def build_graph():
    graph = StateGraph(RCAState)

    graph.add_node("classify", classify_node)
    graph.add_node("rag_retrieval", rag_retrieval_node)
    graph.add_node("analyze", analyze_node)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "rag_retrieval")
    graph.add_edge("rag_retrieval", "analyze")
    graph.add_edge("analyze", END)

    return graph.compile()
