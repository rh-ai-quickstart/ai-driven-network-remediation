"""Tests for the RAN RCA LangGraph pipeline."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest

from helpers import CONTRACTS_DIR, SAMPLE_ANOMALY, make_anomaly, make_llm_response

from ran_rca_service.graph import build_graph


def _mock_rag_client():
    mock = MagicMock()
    mock.search = AsyncMock(return_value=[])
    return patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock)


def _mock_llm():
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=make_llm_response())
    return patch("ran_rca_service.nodes.analyze.get_llm", return_value=mock)


class TestFullGraph:
    @pytest.mark.asyncio
    async def test_invoke_returns_enriched_state(self):
        with _mock_rag_client(), _mock_llm():
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert result["cell_id"] == 42
        assert result["band"] == "Band 29"
        assert result["anomaly_type"] == "LowRsrp"
        assert result["anomaly"] == SAMPLE_ANOMALY["anomaly"]
        assert result["context_snippets"] == []
        assert SAMPLE_ANOMALY["anomaly"] in result["rag_query_used"]
        assert result["root_cause"] != ""
        assert result["recommended_fix"] != ""

    @pytest.mark.asyncio
    async def test_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)

        with _mock_rag_client(), _mock_llm():
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
        }
        validator.validate(enriched)

    @pytest.mark.asyncio
    async def test_different_anomaly_types_all_enrich(self):
        with _mock_rag_client(), _mock_llm():
            graph = build_graph()
            for anomaly_type in ["SinrDegradation", "ThroughputDrop", "CellOutage"]:
                anomaly = make_anomaly(anomaly_type=anomaly_type, anomaly=f"{anomaly_type} detected")
                result = await graph.ainvoke(anomaly)
                assert result["anomaly_type"] == anomaly_type
                assert result["root_cause"] != ""

    @pytest.mark.asyncio
    async def test_llm_failure_anomaly_flows_through(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=ConnectionError("LLM down"))

        with (
            _mock_rag_client(),
            patch("ran_rca_service.nodes.analyze.get_llm", return_value=mock_llm),
        ):
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert result["cell_id"] == 42
        assert result["anomaly"] == SAMPLE_ANOMALY["anomaly"]
        assert result["root_cause"] == ""
        assert result["recommended_fix"] == ""

    @pytest.mark.asyncio
    async def test_both_rag_and_llm_unavailable(self):
        mock_rag = MagicMock()
        mock_rag.search = AsyncMock(side_effect=ConnectionError("vector store down"))
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=ConnectionError("LLM down"))

        with (
            patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock_rag),
            patch("ran_rca_service.nodes.analyze.get_llm", return_value=mock_llm),
        ):
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert result["cell_id"] == 42
        assert result["anomaly"] == SAMPLE_ANOMALY["anomaly"]
        assert result["root_cause"] == ""
        assert result["recommended_fix"] == ""
