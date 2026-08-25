from unittest.mock import AsyncMock, patch

import pytest
from helpers import make_log_event, make_rca, make_state

from agent_service.config import FAST_PATH_LAST_HEAL_ANNOTATION
from agent_service.models import GraphConfig
from agent_service.nodes.remediate import make_remediate_node


class TestFastPathSkip:
    @pytest.mark.asyncio
    async def test_skips_aap_when_spoke_fast_path_recent(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        state = make_state(
            log_event=make_log_event(pod_name="edge-nginx-6b7f8c9d4-x2k9z", namespace="dark-noc-edge"),
            root_cause_analysis=make_rca(failure_type="OOMKilled", recommended_actions=["scale memory"]),
        )
        recent = {"annotations": {FAST_PATH_LAST_HEAL_ANNOTATION: "2026-08-18T12:00:00Z"}}

        with (
            patch(
                "agent_service.nodes.remediate.spoke_fast_path_recent",
                AsyncMock(return_value=True),
            ) as recent_mock,
            patch("agent_service.nodes.remediate._invoke_tool", AsyncMock()) as launch_mock,
        ):
            result = await node(state)

        launch_mock.assert_not_called()
        assert recent_mock.await_args.kwargs["deployment"] == "edge-nginx"
        assert result["fast_path_actuation"] == "spoke"
        assert result["remediation_result"].action_taken == "fast_path_skip"
        assert result["remediation_result"].success is True

    @pytest.mark.asyncio
    async def test_launches_aap_when_fast_path_not_recent(self):
        config = GraphConfig()
        node = make_remediate_node(config)
        state = make_state(
            log_event=make_log_event(pod_name="edge-nginx-6b7f8c9d4-x2k9z", namespace="dark-noc-edge"),
            root_cause_analysis=make_rca(failure_type="OOMKilled", recommended_actions=["scale memory"]),
        )

        async def mock_invoke(tool_name, kwargs):
            if tool_name == "launch_job":
                return {"success": True, "job_id": 42, "status": "pending"}
            if tool_name == "get_job_status":
                return {
                    "success": True,
                    "job_id": 42,
                    "status": "successful",
                    "elapsed": 1.0,
                    "finished": "2024-01-01T00:01:00Z",
                    "failed": False,
                }
            if tool_name == "get_job_output":
                return {"success": True, "output": "ok", "job_id": 42}
            raise ValueError(tool_name)

        with (
            patch(
                "agent_service.nodes.remediate.spoke_fast_path_recent",
                AsyncMock(return_value=False),
            ),
            patch("agent_service.nodes.remediate._invoke_tool", AsyncMock(side_effect=mock_invoke)),
        ):
            result = await node(state)

        assert result["remediation_result"].action_taken == "scale-up-workers"
        assert result["remediation_result"].success is True
