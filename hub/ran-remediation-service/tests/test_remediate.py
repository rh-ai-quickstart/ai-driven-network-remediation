"""Unit tests for the remediate node."""

from unittest.mock import AsyncMock, patch

import pytest

from ran_remediation_service.models import RemediationState
from ran_remediation_service.nodes.remediate import remediate_node


def _state(template="ran-antenna-tilt-adjust"):
    return RemediationState(
        incident_id="a3f7c2d1",
        zone="A",
        application="File",
        ad_label="anomalous",
        ad_confidence=0.9995,
        root_cause="Signal degradation consistent with antenna misalignment.",
        recommended_fix="Adjust antenna tilt per vendor guide Section 4.3.2.",
        template_name=template,
        extra_vars={"incident_id": "a3f7c2d1", "zone": "A", "application": "File"},
    )


@pytest.mark.asyncio
async def test_successful_remediation():
    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool:
        mock_tool.side_effect = [
            {"success": True, "job_id": 99},
            {"status": "successful", "failed": False, "elapsed": 1.5, "finished": "2026-09-21T10:00:00Z"},
            {"output": "PLAY RECAP ok=2 changed=1"},
        ]
        result = await remediate_node(_state())

    assert result["success"] is True
    assert result["job_id"] == "99"
    assert result["job_status"] == "successful"
    assert "PLAY RECAP" in result["output_summary"]


@pytest.mark.asyncio
async def test_launch_failure():
    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool:
        mock_tool.return_value = {"success": False, "error": "Template not found"}
        result = await remediate_node(_state())

    assert result["success"] is False
    assert "Template not found" in result["output_summary"]


@pytest.mark.asyncio
async def test_job_failure():
    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool:
        mock_tool.side_effect = [
            {"success": True, "job_id": 5},
            {"status": "failed", "failed": True, "elapsed": 2.0, "finished": "2026-09-21T10:00:00Z", "result_traceback": "error occurred"},
            {"output": ""},
        ]
        result = await remediate_node(_state())

    assert result["success"] is False
    assert result["job_status"] == "failed"


@pytest.mark.asyncio
async def test_job_timeout():
    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool, \
         patch("ran_remediation_service.nodes.remediate.JOB_TIMEOUT_SECONDS", 0.01), \
         patch("ran_remediation_service.nodes.remediate.POLL_INTERVAL_SECONDS", 0.001):
        mock_tool.side_effect = [
            {"success": True, "job_id": 7},
            {"status": "running"},
        ]
        result = await remediate_node(_state())

    assert result["success"] is False
    assert result["timed_out"] is True


@pytest.mark.asyncio
async def test_launch_exception_returns_failure():
    with patch("ran_remediation_service.nodes.remediate.invoke_tool", side_effect=RuntimeError("network error")):
        result = await remediate_node(_state())

    assert result["success"] is False
    assert "network error" in result["output_summary"]
