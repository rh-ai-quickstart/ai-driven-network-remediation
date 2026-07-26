from unittest.mock import AsyncMock, patch

from agent_service.models import GraphConfig
from agent_service.nodes.investigate import make_investigate_node
from helpers import make_state


def _llm_no_tool_call():
    """LLM response with no tool calls — investigation complete."""
    response = AsyncMock()
    response.tool_calls = []
    response.content = "No further investigation needed."
    return response


def _llm_with_tool_call(name="get_events", args=None, call_id="call-1"):
    """LLM response requesting a single tool call."""
    response = AsyncMock()
    response.tool_calls = [{"name": name, "args": args or {"namespace": "prod", "limit": 20}, "id": call_id}]
    response.content = ""
    return response


_STUB_EVENTS = {
    "items": [
        {"reason": "OOMKilled", "message": "container killed", "metadata": {"namespace": "prod"}},
        {"reason": "Pulling", "message": "pulling image", "metadata": {"namespace": "prod"}},
    ]
}


class TestInvestigateNode:
    async def test_no_tool_call_returns_empty_cluster_events(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state(pod_status={"items": [{"metadata": {"name": "nginx-abc"}}]})

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_no_tool_call())
        with patch("agent_service.nodes.investigate._llm", mock_llm):
            result = await node(state)

        assert result["cluster_events"] == []

    async def test_single_tool_call_populates_cluster_events(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state(pod_status={"items": [{"metadata": {"name": "nginx-abc"}}]})

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[_llm_with_tool_call(), _llm_no_tool_call()])
        mock_invoke = AsyncMock(return_value=_STUB_EVENTS)
        with (
            patch("agent_service.nodes.investigate._llm", mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            result = await node(state)

        assert len(result["cluster_events"]) == 2
        assert result["cluster_events"][0]["reason"] == "OOMKilled"
        mock_invoke.assert_called_once_with("get_events", {"namespace": "prod", "limit": 20})

    async def test_tool_failure_feeds_error_back_and_continues(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[_llm_with_tool_call(), _llm_no_tool_call()])
        mock_invoke = AsyncMock(side_effect=ConnectionError("connection refused"))
        with (
            patch("agent_service.nodes.investigate._llm", mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            result = await node(state)

        assert result["cluster_events"] == []
        assert mock_llm.ainvoke.call_count == 2
        last_call_messages = mock_llm.ainvoke.call_args_list[1][0][0]
        tool_msg = last_call_messages[-1]
        assert "connection refused" in tool_msg.content

    async def test_iteration_cap_stops_loop(self):
        config = GraphConfig(investigate_max_iterations=2)
        node = make_investigate_node(config)
        state = make_state()

        always_call = _llm_with_tool_call()
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=always_call)
        mock_invoke = AsyncMock(return_value=_STUB_EVENTS)
        with (
            patch("agent_service.nodes.investigate._llm", mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            result = await node(state)

        assert mock_llm.ainvoke.call_count == 2
        assert len(result["cluster_events"]) > 0

    async def test_llm_error_returns_partial_evidence(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state(cluster_events=[{"reason": "pre-existing"}])

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))
        with patch("agent_service.nodes.investigate._llm", mock_llm):
            result = await node(state)

        assert result["cluster_events"] == [{"reason": "pre-existing"}]

    async def test_timeout_writes_partial_evidence_and_does_not_raise(self):
        import time

        config = GraphConfig(investigate_timeout=1)
        node = make_investigate_node(config)
        state = make_state(cluster_events=[{"reason": "pre-existing"}])

        async def _slow_llm(*args, **kwargs):
            import asyncio
            await asyncio.sleep(60)
            return _llm_with_tool_call()

        mock_llm = AsyncMock()
        mock_llm.ainvoke = _slow_llm
        t0 = time.monotonic()
        with patch("agent_service.nodes.investigate._llm", mock_llm):
            result = await node(state)
        elapsed = time.monotonic() - t0

        assert elapsed < 5
        assert "cluster_events" in result
        assert result["cluster_events"] == [{"reason": "pre-existing"}]
