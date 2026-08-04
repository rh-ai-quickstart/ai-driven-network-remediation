"""Tests for the RAN RCA LangGraph pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest

from helpers import SAMPLE_ANOMALY, make_anomaly, make_state
from ran_rca_service.graph import analyze_node, build_graph

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


def _mock_rag_client():
    mock_client = MagicMock()
    mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))
    patch_client = patch("ran_rca_service.nodes.rag_retrieval._client", mock_client)
    patch_store_id = patch("ran_rca_service.nodes.rag_retrieval._vector_store_id", "vs-test")
    return patch_client, patch_store_id


class TestAnalyzeNode:
    def test_sets_stub_root_cause(self):
        result = analyze_node(make_state())
        assert "Stub" in result["root_cause"]

    def test_sets_stub_recommended_fix(self):
        result = analyze_node(make_state())
        assert "Stub" in result["recommended_fix"]


class TestFullGraph:
    @pytest.mark.asyncio
    async def test_invoke_returns_enriched_state(self):
        patch_client, patch_store_id = _mock_rag_client()
        with patch_client, patch_store_id:
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
        schema_path = CONTRACTS_DIR / "ran-anomaly-enriched.schema.json"
        schema = json.loads(schema_path.read_text())
        validator = jsonschema.Draft202012Validator(schema)

        patch_client, patch_store_id = _mock_rag_client()
        with patch_client, patch_store_id:
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
        patch_client, patch_store_id = _mock_rag_client()
        with patch_client, patch_store_id:
            graph = build_graph()
            for anomaly_type in ["SinrDegradation", "ThroughputDrop", "CellOutage"]:
                anomaly = make_anomaly(anomaly_type=anomaly_type, anomaly=f"{anomaly_type} detected")
                result = await graph.ainvoke(anomaly)
                assert result["anomaly_type"] == anomaly_type
                assert result["root_cause"] != ""
