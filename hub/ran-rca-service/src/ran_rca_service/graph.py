"""LangGraph RCA pipeline: START -> rag_retrieval -> analyze -> END (stub nodes)."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ran_rca_service.models import RCAState


def rag_retrieval_node(state: RCAState) -> dict:
    return {
        "context_snippets": [],
        "rag_query_used": state.anomaly,
    }


def analyze_node(state: RCAState) -> dict:
    return {
        "root_cause": "Stub root cause — LLM analysis not yet implemented.",
        "recommended_fix": "Stub fix — LLM analysis not yet implemented.",
    }


def build_graph():
    graph = StateGraph(RCAState)

    graph.add_node("rag_retrieval", rag_retrieval_node)
    graph.add_node("analyze", analyze_node)

    graph.add_edge(START, "rag_retrieval")
    graph.add_edge("rag_retrieval", "analyze")
    graph.add_edge("analyze", END)

    return graph.compile()
