"""Tests for the RAN RCA LangGraph pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from helpers import SAMPLE_ANOMALY, make_anomaly, make_state
from ran_rca_service.graph import analyze_node, build_graph, rag_retrieval_node

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


class TestRagRetrievalNode:
    def test_sets_empty_context_snippets(self):
        result = rag_retrieval_node(make_state(anomaly="Low RSRP"))
        assert result["context_snippets"] == []

    def test_sets_rag_query_to_anomaly_string(self):
        result = rag_retrieval_node(make_state(anomaly="Low RSRP: -125 dBm"))
        assert result["rag_query_used"] == "Low RSRP: -125 dBm"

    def test_handles_empty_anomaly(self):
        result = rag_retrieval_node(make_state(anomaly=""))
        assert result["rag_query_used"] == ""


class TestAnalyzeNode:
    def test_sets_stub_root_cause(self):
        result = analyze_node(make_state())
        assert "Stub" in result["root_cause"]

    def test_sets_stub_recommended_fix(self):
        result = analyze_node(make_state())
        assert "Stub" in result["recommended_fix"]


class TestFullGraph:
    def test_invoke_returns_enriched_state(self):
        graph = build_graph()
        result = graph.invoke(SAMPLE_ANOMALY)

        assert result["cell_id"] == 42
        assert result["band"] == "Band 29"
        assert result["anomaly_type"] == "LowRsrp"
        assert result["anomaly"] == SAMPLE_ANOMALY["anomaly"]
        assert result["context_snippets"] == []
        assert result["rag_query_used"] == SAMPLE_ANOMALY["anomaly"]
        assert result["root_cause"] != ""
        assert result["recommended_fix"] != ""

    def test_output_matches_enriched_schema(self):
        schema_path = CONTRACTS_DIR / "ran-anomaly-enriched.schema.json"
        schema = json.loads(schema_path.read_text())
        validator = jsonschema.Draft202012Validator(schema)

        graph = build_graph()
        result = graph.invoke(SAMPLE_ANOMALY)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
        }
        validator.validate(enriched)

    def test_different_anomaly_types_all_enrich(self):
        graph = build_graph()
        for anomaly_type in ["SinrDegradation", "ThroughputDrop", "CellOutage"]:
            anomaly = make_anomaly(anomaly_type=anomaly_type, anomaly=f"{anomaly_type} detected")
            result = graph.invoke(anomaly)
            assert result["anomaly_type"] == anomaly_type
            assert result["root_cause"] != ""
