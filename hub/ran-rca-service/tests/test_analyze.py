"""Tests for the analyze node — real LLM call with graceful degradation."""

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
    async def test_returns_root_cause_from_llm(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        result = await analyze_node(make_state(context_snippets=["vendor doc snippet"]))

        assert "RSRP degradation" in result["root_cause"]
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
        assert "Low RSRP" in user_msg
        assert "vendor doc about RSRP" in user_msg

    @pytest.mark.asyncio
    async def test_llm_called_with_json_schema_response_format(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        await analyze_node(make_state(context_snippets=["ctx"]))

        call_kwargs = mock_llm.ainvoke.call_args[1]
        assert call_kwargs["response_format"]["type"] == "json_schema"


class TestContextTruncation:
    @pytest.mark.asyncio
    async def test_truncates_context_to_max_chars(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        await analyze_node(make_state(context_snippets=["x" * 6000]))

        user_msg = mock_llm.ainvoke.call_args[0][0][1].content
        context_section = user_msg.split("Vendor documentation context:\n")[1]
        assert len(context_section) <= 5000


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_llm_error_returns_empty_fields(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=ConnectionError("LLM unreachable"))

        with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
            from ran_rca_service.nodes.analyze import analyze_node

            result = await analyze_node(make_state(context_snippets=["some context"]))

        assert result["root_cause"] == ""
        assert result["recommended_fix"] == ""

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty_fields(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=make_llm_response(content="not json"))

        with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
            from ran_rca_service.nodes.analyze import analyze_node

            result = await analyze_node(make_state())

        assert result["root_cause"] == ""
        assert result["recommended_fix"] == ""

    @pytest.mark.asyncio
    async def test_no_context_snippets_still_calls_llm(self, mock_llm):
        from ran_rca_service.nodes.analyze import analyze_node

        result = await analyze_node(make_state(context_snippets=[]))

        mock_llm.ainvoke.assert_called_once()
        assert result["root_cause"] != ""

    @pytest.mark.asyncio
    async def test_empty_anomaly_with_llm_error_flows_through(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=TimeoutError("timeout"))

        with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
            from ran_rca_service.nodes.analyze import analyze_node

            result = await analyze_node(make_state(anomaly="", context_snippets=[]))

        assert result["root_cause"] == ""
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

    @pytest.mark.asyncio
    async def test_degraded_output_matches_schema(self):
        schema = json.loads((CONTRACTS_DIR / "ran-anomaly-enriched.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)

        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=ConnectionError("down"))

        with patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm):
            from ran_rca_service.nodes.analyze import analyze_node

            state = make_state()
            result = await analyze_node(state)

        validator.validate(project_enriched(state, result))
