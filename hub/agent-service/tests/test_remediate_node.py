from unittest.mock import AsyncMock, patch

import pytest
from helpers import make_log_event, make_rca, make_state

from agent_service.models import GraphConfig
from agent_service.nodes.remediate import _launch_job, make_remediate_node

_LAUNCH_OK = {"success": True, "job_id": 99, "status": "pending"}
_JOB_DONE = {
    "success": True,
    "job_id": 99,
    "status": "successful",
    "elapsed": 3.5,
    "finished": "2024-01-01T00:01:00Z",
    "failed": False,
    "result_traceback": "",
}
_JOB_OUTPUT = {"success": True, "output": "ok", "job_id": 99}


class TestClusterName:
    @pytest.mark.asyncio
    async def test_edge_site_id_always_present(self):
        invoke_mock = AsyncMock(side_effect=lambda t, kw: _LAUNCH_OK)
        with patch("agent_service.nodes.remediate._invoke_tool", invoke_mock):
            result = await _launch_job("restart-nginx", make_log_event())

        assert result["success"] is True
        call_kwargs = invoke_mock.call_args[0][1]
        assert "credential_name" not in call_kwargs
        assert call_kwargs["extra_vars"]["edge_site_id"] == "edge-1"

    @pytest.mark.asyncio
    async def test_end_to_end(self):
        async def mock_invoke(tool_name, kwargs):
            if tool_name == "launch_job":
                assert "credential_name" not in kwargs
                assert kwargs["extra_vars"]["edge_site_id"] == "edge-1"
                return _LAUNCH_OK
            if tool_name == "get_job_status":
                return _JOB_DONE
            if tool_name == "get_job_output":
                return _JOB_OUTPUT
            raise ValueError(f"Unexpected tool: {tool_name}")

        config = GraphConfig()
        node = make_remediate_node(config)
        state = make_state(
            root_cause_analysis=make_rca(recommended_actions=["restart nginx"]),
        )
        with patch("agent_service.nodes.remediate._invoke_tool", AsyncMock(side_effect=mock_invoke)):
            result = await node(state)

        assert result["remediation_result"].success is True
