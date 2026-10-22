"""Tests for the RAN RCA LangGraph pipeline."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest
from helpers import CONTRACTS_DIR, SAMPLE_ANOMALY, make_llm_response
from ran_rca_service.graph import build_graph


def _mock_rag_client():
    mock = MagicMock()
    mock.search = AsyncMock(return_value=[])
    return patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock)


def _mock_llm():
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=make_llm_response())
    return patch("ran_rca_service.nodes.analyze.get_llm", return_value=mock)


def _mock_classifier():
    return patch("ran_rca_service.graph.classify_node", new=AsyncMock(return_value={
        "ml_class": "Antenna Failure",
        "ml_class_index": 0,
        "ml_confidence": 0.9,
        "ml_status": "used",
        "ml_steer_used": True,
    }))


class TestFullGraph:
    @pytest.mark.asyncio
    async def test_ml_disagreement_keeps_mantis_operational_category(self):
        """The LLM explanation cannot change the classifier's operational category."""
        with (
            patch("ran_rca_service.nodes.classify.classify_node") as classifier,
            _mock_rag_client(),
            _mock_llm(),
        ):
            classifier.return_value = {
                "ml_class": "Co-Channel Interference (Severe)",
                "ml_class_index": 2,
                "ml_confidence": 0.95,
                "ml_status": "used",
                "ml_steer_used": True,
            }
            # Compile with a deterministic classify node to isolate the LLM disagreement.
            from langgraph.graph import END, START, StateGraph
            from ran_rca_service.models import RCAState
            from ran_rca_service.nodes.analyze import analyze_node
            from ran_rca_service.nodes.rag_retrieval import rag_retrieval_node

            graph = StateGraph(RCAState)
            graph.add_node("classify", classifier)
            graph.add_node("rag_retrieval", rag_retrieval_node)
            graph.add_node("analyze", analyze_node)
            graph.add_edge(START, "classify")
            graph.add_edge("classify", "rag_retrieval")
            graph.add_edge("rag_retrieval", "analyze")
            graph.add_edge("analyze", END)
            result = await graph.compile().ainvoke(SAMPLE_ANOMALY)

        assert result["ml_class_index"] == 2
        assert result["ml_status"] == "used"
        assert result["ml_steer_used"] is True
        assert "Co-Channel Interference" in result["rag_query_used"]
        assert result["root_cause_category"] == "interference"

    @pytest.mark.asyncio
    async def test_invoke_returns_enriched_state(self):
        with _mock_classifier(), _mock_rag_client(), _mock_llm():
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert result["incident_id"] == "test-001"
        assert result["zone"] == "A"
        assert result["application"] == "Twitch"
        assert result["ad_label"] == "anomalous"
        assert result["ad_confidence"] == 0.94
        assert result["context_snippets"] == []
        assert result["root_cause_category"] == "cell_failure"
        assert result["root_cause"] != ""
        assert result["recommended_fix"] != ""

    @pytest.mark.asyncio
    async def test_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)

        with _mock_classifier(), _mock_rag_client(), _mock_llm():
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        enriched = {
            "incident_id": result["incident_id"],
            "zone": result["zone"],
            "application": result["application"],
            "kpi_window": result["kpi_window"],
            "ad_label": result["ad_label"],
            "ad_confidence": result["ad_confidence"],
            "root_cause_category": result["root_cause_category"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_class": result.get("ml_class", ""),
            "ml_confidence": result.get("ml_confidence"),
        }
        validator.validate(enriched)

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_classified_category_and_unavailable_explanation(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=ConnectionError("LLM down"))

        with (
            _mock_rag_client(),
            _mock_classifier(),
            patch("ran_rca_service.nodes.analyze.get_llm", return_value=mock_llm),
        ):
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert result["root_cause_category"] == "cell_failure"
        assert result["root_cause"] == ""
        assert result["recommended_fix"] == ""
        assert result["llm_error_reason"] == "LLM unavailable"

    @pytest.mark.asyncio
    async def test_rag_failure_raises(self):
        mock_rag = MagicMock()
        mock_rag.search = AsyncMock(side_effect=ConnectionError("vector store down"))

        with _mock_classifier(), patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock_rag):
            graph = build_graph()
            with pytest.raises(ConnectionError):
                await graph.ainvoke(SAMPLE_ANOMALY)
