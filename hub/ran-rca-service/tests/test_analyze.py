"""Tests for the analyze node — LLM call and failure propagation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import jsonschema
import pytest
from helpers import CONTRACTS_DIR, make_llm_response, make_state, project_enriched


@pytest.fixture()
def mock_llm():
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=make_llm_response())
    with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
        yield llm


class TestSuccessfulLlmCall:
    @pytest.mark.asyncio
    async def test_valid_low_confidence_keeps_mantis_route_and_incident_evidence(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        result = await analyze_node(make_state(
            ml_class="Antenna Failure", ml_class_index=0,
            ml_confidence=0.001, ml_status="used", ml_steer_used=True,
            context_snippets=["vendor RF guide section 4"],
        ))
        prompt = mock_llm.ainvoke.call_args[0][0][1].content
        assert "Mantis RCA prediction: Antenna Failure" in prompt
        assert "Top 8 most variable KPIs" in prompt
        assert "Critical absolute indicators" in prompt
        assert "vendor RF guide section 4" in prompt
        assert result["root_cause_category"] == "cell_failure"

    @pytest.mark.asyncio
    async def test_confident_ml_hint_preserves_kpis_and_vendor_evidence(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        await analyze_node(make_state(
            ml_class="Co-Channel Interference (Severe)",
            ml_class_index=2,
            ml_confidence=0.95,
            ml_status="used",
            ml_steer_used=True,
            context_snippets=["vendor RF guide section 4"],
        ))
        user_msg = mock_llm.ainvoke.call_args[0][0][1].content
        assert "Mantis RCA prediction: Co-Channel Interference (Severe)" in user_msg
        assert "Top 8 most variable KPIs" in user_msg
        assert "Critical absolute indicators" in user_msg
        assert "vendor RF guide section 4" in user_msg

    @pytest.mark.asyncio
    async def test_llm_cannot_select_root_cause_category(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        result = await analyze_node(make_state(context_snippets=["vendor doc snippet"]))

        assert result["root_cause_category"] == "unknown"

    @pytest.mark.asyncio
    async def test_returns_root_cause_from_llm(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        result = await analyze_node(make_state(context_snippets=["vendor doc snippet"]))

        assert "signal degradation" in result["root_cause"].lower()
        assert "3GPP" in result["root_cause"]

    @pytest.mark.asyncio
    async def test_returns_recommended_fix_from_llm(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        result = await analyze_node(make_state(context_snippets=["vendor doc snippet"]))

        assert "Section 4.3.2" in result["recommended_fix"]

    @pytest.mark.asyncio
    async def test_llm_receives_system_and_user_messages(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        await analyze_node(make_state(context_snippets=["some context"]))

        messages = mock_llm.ainvoke.call_args[0][0]
        assert len(messages) == 2
        system_msg = messages[0].content
        assert "RAN" in system_msg or "radio" in system_msg.lower()

    @pytest.mark.asyncio
    async def test_user_message_contains_anomaly_and_context(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        await analyze_node(make_state(context_snippets=["vendor doc about RSRP"]))

        user_msg = mock_llm.ainvoke.call_args[0][0][1].content
        assert "test-001" in user_msg
        assert "Zone: A" in user_msg
        assert "vendor doc about RSRP" in user_msg

    @pytest.mark.asyncio
    async def test_llm_called_with_json_schema_response_format(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        await analyze_node(make_state(context_snippets=["ctx"]))

        call_kwargs = mock_llm.ainvoke.call_args[1]
        assert call_kwargs["response_format"]["type"] == "json_schema"
        schema = call_kwargs["response_format"]["json_schema"]["schema"]
        assert "root_cause_category" not in schema["properties"]
        assert schema["required"] == ["root_cause", "recommended_fix"]


class TestContextTruncation:
    @pytest.mark.asyncio
    async def test_truncates_context_to_max_chars(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        await analyze_node(make_state(context_snippets=["x" * 6000]))

        user_msg = mock_llm.ainvoke.call_args[0][0][1].content
        context_section = user_msg.split("Vendor documentation context:\n")[1]
        assert len(context_section) <= 5000


class TestLlmFailureAvailability:
    @pytest.mark.asyncio
    async def test_connection_error_marks_explanation_unavailable(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=ConnectionError("LLM unreachable"))

        with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
            from ran_rca_service.nodes.analyze import analyze_node

            result = await analyze_node(make_state(context_snippets=["some context"]))
        assert result["root_cause"] == ""
        assert result["recommended_fix"] == ""
        assert result["llm_error_reason"] == "LLM unavailable"

    @pytest.mark.asyncio
    async def test_malformed_json_marks_explanation_unavailable(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=make_llm_response(content="not json"))

        with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
            from ran_rca_service.nodes.analyze import analyze_node

            result = await analyze_node(make_state())
        assert result["llm_error_reason"] == "invalid LLM response"
        assert result["root_cause"] == ""

    @pytest.mark.asyncio
    async def test_no_context_snippets_still_calls_llm(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        result = await analyze_node(make_state(context_snippets=[]))

        mock_llm.ainvoke.assert_called_once()
        assert result["root_cause"] != ""

    @pytest.mark.asyncio
    async def test_timeout_error_marks_explanation_unavailable(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=TimeoutError("timeout"))

        with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
            from ran_rca_service.nodes.analyze import analyze_node

            result = await analyze_node(make_state(anomaly="", context_snippets=[]))
        assert result["llm_error_reason"] == "LLM timed out"
        assert result["recommended_fix"] == ""


class TestContractValidation:
    @pytest.mark.asyncio
    async def test_enriched_output_matches_schema(self, mock_llm):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)

        from ran_rca_service.nodes.analyze import analyze_node

        state = make_state(context_snippets=["some context"])
        result = await analyze_node(state)

        validator.validate(project_enriched(state, result))
