from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from helpers import make_state

from agent_service.models import RootCauseAnalysis

_VALID_RCA_JSON = (
    '{"failure_type": "OOMKilled", "confidence": 0.92, '
    '"summary": "Container killed by OOM", '
    '"evidence": ["memory spike at 14:32"], '
    '"recommended_actions": ["increase memory limit"], '
    '"estimated_severity": "high", '
    '"runbook_reference": "runbook-oom-001"}'
)


def _make_llm_response(content=_VALID_RCA_JSON, input_tokens=100, output_tokens=50):
    msg = MagicMock()
    msg.content = content
    msg.usage_metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    return msg


class TestSuccessfulLlmCall:
    @pytest.mark.asyncio
    async def test_returns_valid_rca_from_llm_response(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response())

        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(context_snippets=["some runbook context"])
            result = await analyze_node(state)

        rca = result["root_cause_analysis"]
        assert isinstance(rca, RootCauseAnalysis)
        assert rca.failure_type == "OOMKilled"
        assert rca.confidence == 0.92
        assert rca.summary == "Container killed by OOM"
        assert rca.estimated_severity == "high"


class TestRagContextTruncation:
    @pytest.mark.asyncio
    async def test_truncates_context_to_5000_chars(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response())

        long_snippet = "x" * 6000
        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(context_snippets=[long_snippet])
            await analyze_node(state)

        call_args = mock_llm.ainvoke.call_args
        messages = call_args[0][0]
        user_msg = messages[1].content
        context_section = user_msg.split("RAG context:\n")[1]
        assert len(context_section) <= 5000


class TestTokenAndLatencyTracking:
    @pytest.mark.asyncio
    async def test_sets_token_count_and_latency(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response(input_tokens=200, output_tokens=80))

        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(context_snippets=["context"])
            result = await analyze_node(state)

        assert result["analysis_tokens_used"] == 280
        assert result["analysis_latency_ms"] > 0


class TestLlmErrorFallback:
    @pytest.mark.asyncio
    async def test_llm_error_returns_fallback_rca(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=ConnectionError("vLLM unreachable"))

        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(context_snippets=["some context"])
            result = await analyze_node(state)

        rca = result["root_cause_analysis"]
        assert isinstance(rca, RootCauseAnalysis)
        assert rca.confidence == 0.0
        assert rca.failure_type == "Unknown"
        assert rca.estimated_severity == "critical"


class TestEvidenceInPrompt:
    @pytest.mark.asyncio
    async def test_evidence_appears_in_llm_prompt(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response())

        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(
                context_snippets=["some context"],
                recent_errors=[{"pattern": "OOMKilled", "count": 3}],
            )
            await analyze_node(state)

        call_args = mock_llm.ainvoke.call_args
        messages = call_args[0][0]
        user_msg = messages[1].content
        assert "Investigation evidence:" in user_msg
        assert "Recent Errors" in user_msg
        assert "OOMKilled" in user_msg

    @pytest.mark.asyncio
    async def test_empty_evidence_no_regression(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response())

        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(context_snippets=["some context"])
            await analyze_node(state)

        call_args = mock_llm.ainvoke.call_args
        messages = call_args[0][0]
        user_msg = messages[1].content
        assert "Investigation evidence:" not in user_msg
        assert user_msg.startswith("Log event:")


class TestOverrideBypass:
    @pytest.mark.asyncio
    async def test_override_pins_type_and_confidence_on_llm_result(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response())

        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(
                confidence_override=0.42,
                failure_type_override="DNSFailure",
            )
            result = await analyze_node(state)

        mock_llm.ainvoke.assert_called_once()
        rca = result["root_cause_analysis"]
        assert isinstance(rca, RootCauseAnalysis)
        assert rca.confidence == 0.42
        assert rca.failure_type == "DNSFailure"
        assert rca.summary == "Container killed by OOM"

    @pytest.mark.asyncio
    async def test_override_fallback_when_llm_fails(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=ConnectionError("unreachable"))

        with patch("agent_service.nodes.analyze.get_llm", return_value=mock_llm):
            from agent_service.nodes.analyze import analyze_node

            state = make_state(
                confidence_override=0.42,
                failure_type_override="DNSFailure",
            )
            result = await analyze_node(state)

        rca = result["root_cause_analysis"]
        assert rca.confidence == 0.42
        assert rca.failure_type == "DNSFailure"
        assert rca.estimated_severity == "medium"
