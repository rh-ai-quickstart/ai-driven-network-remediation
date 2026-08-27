"""Tests for the RAN RCA LangGraph pipeline."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jsonschema
import pytest
from helpers import (
    CONTRACTS_DIR,
    SAMPLE_ANOMALY,
    SAMPLE_CLASSIFY_RESPONSE,
    SAMPLE_KPI_WINDOW,
    make_anomaly,
    make_llm_response,
)
from ran_rca_service.graph import build_graph


def _mock_rag_client():
    mock = MagicMock()
    mock.search = AsyncMock(return_value=[])
    return patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock)


def _mock_llm():
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=make_llm_response())
    return patch("ran_rca_service.nodes.analyze.get_llm", return_value=mock)


def _disable_mantis():
    return patch("ran_rca_service.nodes.classify.MANTIS_ENABLED", False)


def _enable_mantis(stack: ExitStack, classify_response=SAMPLE_CLASSIFY_RESPONSE, *, status_code=200):
    """Enable MANTIS and mock the httpx POST to the classify endpoint.

    Enters all patches into the provided ExitStack so callers don't need
    tuple unpacking inside ``with`` blocks.
    """
    classify_url = "http://classify:8080/v1/classify"
    fake_request = httpx.Request("POST", classify_url)

    async def _fake_post(url, **kwargs):
        return httpx.Response(status_code, json=classify_response, request=fake_request)

    mock_client = AsyncMock()
    mock_client.post = _fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    stack.enter_context(patch("ran_rca_service.nodes.classify.MANTIS_ENABLED", True))
    stack.enter_context(patch("ran_rca_service.nodes.classify.CLASSIFY_INFERENCE_URL", classify_url))
    stack.enter_context(patch("ran_rca_service.nodes.classify.httpx.AsyncClient", return_value=mock_client))


def _enable_mantis_with_error(stack: ExitStack, error: Exception):
    """Enable MANTIS but make the httpx POST raise the given exception."""
    classify_url = "http://classify:8080/v1/classify"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=error)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    stack.enter_context(patch("ran_rca_service.nodes.classify.MANTIS_ENABLED", True))
    stack.enter_context(patch("ran_rca_service.nodes.classify.CLASSIFY_INFERENCE_URL", classify_url))
    stack.enter_context(patch("ran_rca_service.nodes.classify.httpx.AsyncClient", return_value=mock_client))


class TestFullGraph:
    @pytest.mark.asyncio
    async def test_invoke_returns_enriched_state(self):
        with _mock_rag_client(), _mock_llm(), _disable_mantis():
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

        with _mock_rag_client(), _mock_llm(), _disable_mantis():
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)

    @pytest.mark.asyncio
    async def test_different_anomaly_types_all_enrich(self):
        with _mock_rag_client(), _mock_llm(), _disable_mantis():
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
            _disable_mantis(),
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
            _disable_mantis(),
        ):
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert result["cell_id"] == 42
        assert result["anomaly"] == SAMPLE_ANOMALY["anomaly"]
        assert result["root_cause"] == ""
        assert result["recommended_fix"] == ""


class TestClassifySteeredGraph:
    """Primary seam: graph invoke with mocked classify HTTP.

    Steered case: classify succeeds, ML fields populated, RAG query uses
    the predicted class, and the LLM prompt includes the ML hint.
    """

    @pytest.mark.asyncio
    async def test_steered_classify_populates_ml_fields(self):
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == "Antenna Failure"
        assert result["ml_confidence"] == 0.92
        assert result["ml_steer_used"] is True
        assert result["root_cause"] != ""
        assert result["recommended_fix"] != ""

    @pytest.mark.asyncio
    async def test_steered_rag_query_uses_ml_class(self):
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        mock_rag = MagicMock()
        mock_rag.search = AsyncMock(return_value=[])

        with ExitStack() as stack:
            stack.enter_context(patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock_rag))
            stack.enter_context(_mock_llm())
            _enable_mantis(stack)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert "Antenna Failure" in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_steered_llm_prompt_includes_ml_hint(self):
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke = AsyncMock(return_value=make_llm_response())

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(patch("ran_rca_service.nodes.analyze.get_llm", return_value=mock_llm_instance))
            _enable_mantis(stack)
            graph = build_graph()
            await graph.ainvoke(anomaly_with_kpi)

        user_msg = mock_llm_instance.ainvoke.call_args[0][0][1].content
        assert "Antenna Failure" in user_msg
        assert "ML classification hint" in user_msg

    @pytest.mark.asyncio
    async def test_steered_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)


class TestClassifyDisabledGraph:
    """Primary seam: MANTIS_ENABLED=false bypasses classify entirely."""

    @pytest.mark.asyncio
    async def test_disabled_classify_leaves_ml_defaults(self):
        with _mock_rag_client(), _mock_llm(), _disable_mantis():
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False

    @pytest.mark.asyncio
    async def test_disabled_classify_rag_uses_identity_query(self):
        mock_rag = MagicMock()
        mock_rag.search = AsyncMock(return_value=[])

        with (
            patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock_rag),
            _mock_llm(),
            _disable_mantis(),
        ):
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        assert "LowRsrp" in result["rag_query_used"]
        assert "Band 29" in result["rag_query_used"]
        assert "Antenna Failure" not in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_disabled_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)

        with _mock_rag_client(), _mock_llm(), _disable_mantis():
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)


class TestClassifyFailureGraph:
    """Classify service failure should not break the pipeline."""

    @pytest.mark.asyncio
    async def test_classify_http_error_falls_through(self):
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, status_code=500)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False
        assert result["root_cause"] != ""
        assert result["recommended_fix"] != ""

    @pytest.mark.asyncio
    async def test_classify_http_error_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, status_code=500)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)

    @pytest.mark.asyncio
    async def test_classify_timeout_falls_through(self):
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis_with_error(stack, httpx.ReadTimeout("classify timed out"))
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False
        assert result["root_cause"] != ""

    @pytest.mark.asyncio
    async def test_classify_connection_error_falls_through(self):
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis_with_error(stack, httpx.ConnectError("connection refused"))
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False
        assert result["root_cause"] != ""


class TestConfidenceGating:
    """Primary seam: confidence below MANTIS_CONFIDENCE_THRESHOLD populates ML
    fields but sets ml_steer_used=false — RAG and analyze run unsteered."""

    @pytest.mark.asyncio
    async def test_low_confidence_populates_ml_fields_but_no_steering(self):
        low_conf_response = {"class": "Antenna Failure", "confidence": 0.3, "class_index": 0}
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, classify_response=low_conf_response)
            stack.enter_context(patch("ran_rca_service.nodes.classify.MANTIS_CONFIDENCE_THRESHOLD", 0.6))
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == "Antenna Failure"
        assert result["ml_confidence"] == 0.3
        assert result["ml_steer_used"] is False

    @pytest.mark.asyncio
    async def test_low_confidence_rag_runs_unsteered(self):
        low_conf_response = {"class": "Antenna Failure", "confidence": 0.3, "class_index": 0}
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        mock_rag = MagicMock()
        mock_rag.search = AsyncMock(return_value=[])

        with ExitStack() as stack:
            stack.enter_context(patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock_rag))
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, classify_response=low_conf_response)
            stack.enter_context(patch("ran_rca_service.nodes.classify.MANTIS_CONFIDENCE_THRESHOLD", 0.6))
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert "Antenna Failure" not in result["rag_query_used"]
        assert "LowRsrp" in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_low_confidence_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        low_conf_response = {"class": "Antenna Failure", "confidence": 0.3, "class_index": 0}
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, classify_response=low_conf_response)
            stack.enter_context(patch("ran_rca_service.nodes.classify.MANTIS_CONFIDENCE_THRESHOLD", 0.6))
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)


class TestEmptyKpiWindow:
    """Primary seam: empty or malformed kpi_window skips classify entirely."""

    @pytest.mark.asyncio
    async def test_empty_kpi_window_produces_ml_defaults(self):
        anomaly_no_kpi = {**SAMPLE_ANOMALY, "kpi_window": []}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_no_kpi)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False
        assert result["root_cause"] != ""

    @pytest.mark.asyncio
    async def test_wrong_shape_kpi_window_produces_ml_defaults(self):
        """kpi_window present but not 128×18 — treated as missing."""
        wrong_shape = [[1.0, 2.0, 3.0] for _ in range(10)]
        anomaly = {**SAMPLE_ANOMALY, "kpi_window": wrong_shape}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack)
            graph = build_graph()
            result = await graph.ainvoke(anomaly)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False
        assert result["root_cause"] != ""

    @pytest.mark.asyncio
    async def test_wrong_shape_kpi_window_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        wrong_shape = [[1.0, 2.0, 3.0] for _ in range(10)]
        anomaly = {**SAMPLE_ANOMALY, "kpi_window": wrong_shape}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack)
            graph = build_graph()
            result = await graph.ainvoke(anomaly)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)

    @pytest.mark.asyncio
    async def test_missing_kpi_window_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack)
            graph = build_graph()
            result = await graph.ainvoke(SAMPLE_ANOMALY)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)


class TestInvalidClassIndex:
    """Primary seam: class_index outside RCA_CLASSES range treated as classify failure."""

    @pytest.mark.asyncio
    async def test_invalid_class_index_produces_ml_defaults(self):
        bad_response = {"class": "Unknown", "confidence": 0.95, "class_index": 999}
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, classify_response=bad_response)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False
        assert result["root_cause"] != ""

    @pytest.mark.asyncio
    async def test_negative_class_index_produces_ml_defaults(self):
        bad_response = {"class": "Antenna Failure", "confidence": 0.9, "class_index": -1}
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, classify_response=bad_response)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False

    @pytest.mark.asyncio
    async def test_negative_class_index_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        bad_response = {"class": "Antenna Failure", "confidence": 0.9, "class_index": -1}
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, classify_response=bad_response)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)

    @pytest.mark.asyncio
    async def test_invalid_class_index_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        bad_response = {"class": "Unknown", "confidence": 0.95, "class_index": 999}
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with ExitStack() as stack:
            stack.enter_context(_mock_rag_client())
            stack.enter_context(_mock_llm())
            _enable_mantis(stack, classify_response=bad_response)
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)


class TestMantisDisabledWithUrl:
    """Primary seam: MANTIS_ENABLED=false with classify URL set still bypasses classify."""

    @pytest.mark.asyncio
    async def test_disabled_with_url_produces_ml_defaults(self):
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with (
            _mock_rag_client(),
            _mock_llm(),
            patch("ran_rca_service.nodes.classify.MANTIS_ENABLED", False),
            patch("ran_rca_service.nodes.classify.CLASSIFY_INFERENCE_URL", "http://classify:8080/v1/classify"),
        ):
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        assert result["ml_root_cause_class"] == ""
        assert result["ml_confidence"] == 0.0
        assert result["ml_steer_used"] is False
        assert result["root_cause"] != ""
        assert result["recommended_fix"] != ""

    @pytest.mark.asyncio
    async def test_disabled_with_url_output_matches_enriched_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        anomaly_with_kpi = {**SAMPLE_ANOMALY, "kpi_window": SAMPLE_KPI_WINDOW}

        with (
            _mock_rag_client(),
            _mock_llm(),
            patch("ran_rca_service.nodes.classify.MANTIS_ENABLED", False),
            patch("ran_rca_service.nodes.classify.CLASSIFY_INFERENCE_URL", "http://classify:8080/v1/classify"),
        ):
            graph = build_graph()
            result = await graph.ainvoke(anomaly_with_kpi)

        enriched = {
            "cell_id": result["cell_id"],
            "band": result["band"],
            "anomaly_type": result["anomaly_type"],
            "anomaly": result["anomaly"],
            "root_cause": result["root_cause"],
            "recommended_fix": result["recommended_fix"],
            "ml_root_cause_class": result["ml_root_cause_class"],
            "ml_confidence": result["ml_confidence"],
            "ml_steer_used": result["ml_steer_used"],
        }
        validator.validate(enriched)
