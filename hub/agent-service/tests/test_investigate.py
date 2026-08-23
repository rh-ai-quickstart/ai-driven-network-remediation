from unittest.mock import AsyncMock, patch

from helpers import make_state

from agent_service.models import GraphConfig
from agent_service.nodes.investigate import _merge_tool_result, make_investigate_node


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


def _llm_with_multi_tool_call():
    """LLM response requesting all four tools at once."""
    response = AsyncMock()
    response.tool_calls = [
        {"name": "get_events", "args": {"namespace": "prod"}, "id": "call-ev"},
        {"name": "find_error_patterns", "args": {"namespace": "prod", "app": "nginx"}, "id": "call-err"},
        {"name": "get_pod_logs", "args": {"pod_name": "nginx-abc", "namespace": "prod"}, "id": "call-logs"},
        {"name": "search_logs", "args": {"namespace": "prod", "text": "error"}, "id": "call-search"},
    ]
    response.content = ""
    return response


_STUB_EVENTS = {
    "items": [
        {"reason": "OOMKilled", "message": "container killed", "metadata": {"namespace": "prod"}},
        {"reason": "Pulling", "message": "pulling image", "metadata": {"namespace": "prod"}},
    ]
}

_STUB_ERROR_PATTERNS = [{"pattern": "OOMKilled", "count": 5}]
_STUB_POD_LOGS = "2024-01-01 ERROR: container killed\n2024-01-01 INFO: restarting"
_STUB_LOG_SEARCH = [{"timestamp": "2024-01-01", "message": "error found", "pod": "nginx-abc"}]


class TestInvestigateNode:
    async def test_no_tool_call_returns_empty_cluster_events(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state(pod_status={"items": [{"metadata": {"name": "nginx-abc"}}]})

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_no_tool_call())
        with patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm):
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
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            result = await node(state)

        assert len(result["cluster_events"]) == 2
        assert result["cluster_events"][0]["reason"] == "OOMKilled"
        mock_invoke.assert_called_once_with("get_events", {"namespace": "prod", "limit": 20, "edge_site_id": "edge-1"})

    async def test_tool_failure_feeds_error_back_and_continues(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[_llm_with_tool_call(), _llm_no_tool_call()])
        mock_invoke = AsyncMock(side_effect=ConnectionError("connection refused"))
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
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
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
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
        with patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm):
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
        with patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm):
            result = await node(state)
        elapsed = time.monotonic() - t0

        assert elapsed < 5
        assert "cluster_events" in result
        assert result["cluster_events"] == [{"reason": "pre-existing"}]

    async def test_multi_tool_response_populates_all_state_fields(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        async def _mock_invoke(tool_name, tool_args):
            if tool_name == "get_events":
                return _STUB_EVENTS
            if tool_name == "find_error_patterns":
                return {"patterns": _STUB_ERROR_PATTERNS}
            if tool_name == "get_pod_logs":
                return {"logs": _STUB_POD_LOGS}
            if tool_name == "search_logs":
                return {"logs": _STUB_LOG_SEARCH}
            return {}

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[_llm_with_multi_tool_call(), _llm_no_tool_call()])
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", _mock_invoke),
        ):
            result = await node(state)

        assert len(result["cluster_events"]) == 2
        assert result["cluster_events"][0]["reason"] == "OOMKilled"
        assert result["recent_errors"] == _STUB_ERROR_PATTERNS
        assert result["pod_logs"] == _STUB_POD_LOGS
        assert result["log_search_results"] == _STUB_LOG_SEARCH

    async def test_multi_tool_calls_execute_concurrently(self):
        import time

        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        call_times = []

        async def _slow_invoke(tool_name, tool_args):
            import asyncio

            call_times.append(time.monotonic())
            await asyncio.sleep(0.3)
            return _STUB_EVENTS if tool_name == "get_events" else {"patterns": []}

        two_tools = AsyncMock()
        two_tools.tool_calls = [
            {"name": "get_events", "args": {"namespace": "prod"}, "id": "c1"},
            {"name": "find_error_patterns", "args": {"namespace": "prod", "app": "x"}, "id": "c2"},
        ]
        two_tools.content = ""

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[two_tools, _llm_no_tool_call()])
        t0 = time.monotonic()
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", _slow_invoke),
        ):
            await node(state)
        elapsed = time.monotonic() - t0

        assert len(call_times) == 2
        assert abs(call_times[1] - call_times[0]) < 0.1, "Tools should start near-simultaneously"
        assert elapsed < 0.8, f"Two 0.3s tools in parallel should take < 0.8s, took {elapsed:.2f}s"

    async def test_per_tool_timeout_returns_error_and_does_not_block(self):
        import asyncio
        import time

        config = GraphConfig(tool_call_timeout=1)
        node = make_investigate_node(config)
        state = make_state()

        async def _invoke_with_one_hang(tool_name, tool_args):
            if tool_name == "get_pod_logs":
                await asyncio.sleep(60)
                return {"logs": "should never reach"}
            return _STUB_EVENTS

        two_tools = AsyncMock()
        two_tools.tool_calls = [
            {"name": "get_events", "args": {"namespace": "prod"}, "id": "c1"},
            {"name": "get_pod_logs", "args": {"pod_name": "x", "namespace": "prod"}, "id": "c2"},
        ]
        two_tools.content = ""

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[two_tools, _llm_no_tool_call()])
        t0 = time.monotonic()
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", _invoke_with_one_hang),
        ):
            result = await node(state)
        elapsed = time.monotonic() - t0

        assert elapsed < 5, f"Should be bounded by per-tool timeout, took {elapsed:.2f}s"
        assert len(result["cluster_events"]) == 2
        assert result["pod_logs"] == ""
        last_call_msgs = mock_llm.ainvoke.call_args_list[1][0][0]
        tool_msgs = [m for m in last_call_msgs if hasattr(m, "tool_call_id")]
        error_msg = [m for m in tool_msgs if "timed out" in m.content]
        assert len(error_msg) == 1, "Timeout error should be fed back to LLM"

    async def test_tool_failure_adaptation_llm_switches_to_alternative(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        async def _invoke_with_failure(tool_name, tool_args):
            if tool_name == "get_pod_logs":
                raise ConnectionError("connection refused")
            if tool_name == "search_logs":
                return {"logs": _STUB_LOG_SEARCH}
            return {}

        iter1_logs_fail = AsyncMock()
        iter1_logs_fail.tool_calls = [
            {"name": "get_pod_logs", "args": {"pod_name": "x", "namespace": "prod"}, "id": "c1"},
        ]
        iter1_logs_fail.content = ""

        iter2_search_fallback = AsyncMock()
        iter2_search_fallback.tool_calls = [
            {"name": "search_logs", "args": {"namespace": "prod", "text": "error"}, "id": "c2"},
        ]
        iter2_search_fallback.content = ""

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[iter1_logs_fail, iter2_search_fallback, _llm_no_tool_call()])
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", _invoke_with_failure),
        ):
            result = await node(state)

        assert result["pod_logs"] == ""
        assert result["log_search_results"] == _STUB_LOG_SEARCH
        assert mock_llm.ainvoke.call_count == 3


class TestToolArgDefaults:
    async def test_incident_namespace_is_not_rewritten(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[_llm_with_tool_call(), _llm_no_tool_call()])
        mock_invoke = AsyncMock(return_value=_STUB_EVENTS)
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            await node(state)

        mock_invoke.assert_called_once_with("get_events", {"namespace": "prod", "limit": 20, "edge_site_id": "edge-1"})

    async def test_discovered_namespace_is_not_rewritten(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        first = _llm_with_tool_call(args={"namespace": "prod", "limit": 20})
        second = _llm_with_tool_call(
            args={"namespace": "payments", "limit": 20},
            call_id="call-2",
        )
        events_with_payments = {
            "items": [
                {"reason": "BackOff", "message": "crash", "metadata": {"namespace": "payments"}},
            ]
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[first, second, _llm_no_tool_call()])
        mock_invoke = AsyncMock(return_value=events_with_payments)
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            await node(state)

        assert mock_invoke.call_args_list[0].args == ("get_events", {"namespace": "prod", "limit": 20, "edge_site_id": "edge-1"})
        assert mock_invoke.call_args_list[1].args == ("get_events", {"namespace": "payments", "limit": 20, "edge_site_id": "edge-1"})

    async def test_llm_provided_edge_site_id_is_preserved(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        tool_call = _llm_with_tool_call(
            name="get_events",
            args={"namespace": "prod", "limit": 20, "edge_site_id": "edge-site-02"},
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[tool_call, _llm_no_tool_call()])
        mock_invoke = AsyncMock(return_value=_STUB_EVENTS)
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            await node(state)

        mock_invoke.assert_called_once_with(
            "get_events", {"namespace": "prod", "limit": 20, "edge_site_id": "edge-site-02"}
        )

    async def test_loki_tool_omits_edge_site_id_openshift_includes_it(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        calls = {}

        async def _record_invoke(tool_name, tool_args):
            calls[tool_name] = tool_args
            if tool_name == "get_events":
                return _STUB_EVENTS
            return {"patterns": []}

        mixed = AsyncMock()
        mixed.tool_calls = [
            {"name": "get_events", "args": {"namespace": "prod"}, "id": "c1"},
            {"name": "search_logs", "args": {"namespace": "prod", "text": "error"}, "id": "c2"},
        ]
        mixed.content = ""

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[mixed, _llm_no_tool_call()])
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", _record_invoke),
        ):
            await node(state)

        assert calls["get_events"]["edge_site_id"] == "edge-1"
        assert "edge_site_id" not in calls["search_logs"]

    async def test_omitted_edge_site_id_defaults_to_log_event(self):
        config = GraphConfig()
        node = make_investigate_node(config)
        state = make_state()

        tool_call = _llm_with_tool_call(
            name="get_events",
            args={"namespace": "prod", "limit": 20},
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[tool_call, _llm_no_tool_call()])
        mock_invoke = AsyncMock(return_value=_STUB_EVENTS)
        with (
            patch("agent_service.nodes.investigate.get_llm", return_value=mock_llm),
            patch("agent_service.nodes.investigate.invoke_tool", mock_invoke),
        ):
            await node(state)

        mock_invoke.assert_called_once_with(
            "get_events", {"namespace": "prod", "limit": 20, "edge_site_id": "edge-1"}
        )


def _empty_evidence():
    """Return a fresh evidence dict matching the structure in investigate_node."""
    return {
        "cluster_events": [],
        "recent_errors": [],
        "pod_logs": "",
        "resource_specs": "",
        "log_search_results": [],
    }


_STUB_POD_SPEC = {
    "name": "myapp-6b7f8c-x2k9z",
    "namespace": "prod",
    "spec": {
        "containers": [
            {
                "name": "myapp",
                "image": "myapp:1.0",
                "resources": {"limits": {"cpu": "500m", "memory": "256Mi"}},
            }
        ]
    },
    "success": True,
    "error": None,
}


class TestMergeToolResult:
    """Tests for _merge_tool_result with the get_pod_spec branch."""

    def test_get_pod_spec_stores_spec_data(self):
        evidence = _empty_evidence()
        _merge_tool_result("get_pod_spec", _STUB_POD_SPEC, evidence)
        assert evidence["resource_specs"] != ""
        assert "myapp" in evidence["resource_specs"]
        assert "500m" in evidence["resource_specs"]

    def test_get_pod_spec_error_leaves_evidence_unchanged(self):
        evidence = _empty_evidence()
        error_result = {
            "name": "gone",
            "namespace": "prod",
            "spec": {},
            "success": False,
            "error": "not found",
        }
        _merge_tool_result("get_pod_spec", error_result, evidence)
        assert evidence["resource_specs"] == ""

    def test_get_pod_spec_appends_to_existing(self):
        evidence = _empty_evidence()
        evidence["resource_specs"] = "Pod: existing-pod\n  limits: cpu: 100m"
        _merge_tool_result("get_pod_spec", _STUB_POD_SPEC, evidence)
        assert "existing-pod" in evidence["resource_specs"]
        assert "myapp" in evidence["resource_specs"]
