from unittest.mock import AsyncMock, patch

import pytest

from agent_service.nodes.enrich import enrich_node
from helpers import make_state


class TestEnrichNode:
    async def test_successful_call_populates_pod_status(self):
        state = make_state()
        pod_data = {"items": [{"metadata": {"name": "nginx-abc123"}, "status": {"phase": "Running"}}]}
        with patch("agent_service.nodes.enrich.invoke_tool", AsyncMock(return_value=pod_data)):
            result = await enrich_node(state)

        assert result["pod_status"] == pod_data

    async def test_failed_call_writes_empty_dict(self):
        state = make_state()
        with patch("agent_service.nodes.enrich.invoke_tool", AsyncMock(side_effect=Exception("connection refused"))):
            result = await enrich_node(state)

        assert result["pod_status"] == {}

    async def test_calls_get_pods_with_correct_namespace(self):
        state = make_state(log_event=make_state().log_event)
        mock_invoke = AsyncMock(return_value={})
        with patch("agent_service.nodes.enrich.invoke_tool", mock_invoke):
            await enrich_node(state)

        mock_invoke.assert_called_once_with("get_pods", {"namespace": "prod"})
