"""Unit tests for the remediate node."""

from unittest.mock import AsyncMock, patch

import pytest

from ran_remediation_service.aap_client import AAPError
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


def _patch_aap(job_id, status, output=""):
    """Patch the three aap_client calls the node makes, in call order."""
    return (
        patch("ran_remediation_service.nodes.remediate.launch_job", new_callable=AsyncMock, return_value=job_id),
        patch("ran_remediation_service.nodes.remediate.get_job_status", new_callable=AsyncMock, return_value=status),
        patch("ran_remediation_service.nodes.remediate.get_job_output", new_callable=AsyncMock, return_value=output),
    )


def _patch_launch_error(exc):
    return patch("ran_remediation_service.nodes.remediate.launch_job", new_callable=AsyncMock, side_effect=exc)


@pytest.mark.asyncio
async def test_successful_remediation():
    launch, status, output = _patch_aap(
        99,
        {"status": "successful", "failed": False, "elapsed": 1.5, "finished": "2026-09-21T10:00:00Z"},
        "PLAY RECAP ok=2 changed=1",
    )
    with launch, status, output:
        result = await remediate_node(_state())

    assert result["success"] is True
    assert result["job_id"] == "99"
    assert result["job_status"] == "successful"
    assert "PLAY RECAP" in result["output_summary"]


@pytest.mark.asyncio
async def test_launch_failure():
    with _patch_launch_error(AAPError("Job template 'ran-antenna-tilt-adjust' not found")):
        result = await remediate_node(_state())

    assert result["success"] is False
    assert "not found" in result["output_summary"]


@pytest.mark.asyncio
async def test_job_failure():
    launch, status, output = _patch_aap(
        5,
        {
            "status": "failed",
            "failed": True,
            "elapsed": 2.0,
            "finished": "2026-09-21T10:00:00Z",
            "result_traceback": "error occurred",
        },
    )
    with launch, status, output:
        result = await remediate_node(_state())

    assert result["success"] is False
    assert result["job_status"] == "failed"


@pytest.mark.asyncio
async def test_job_timeout():
    launch, status, output = _patch_aap(7, {"status": "running"})
    with launch, status, output, \
         patch("ran_remediation_service.nodes.remediate.JOB_TIMEOUT_SECONDS", 0.01), \
         patch("ran_remediation_service.nodes.remediate.POLL_INTERVAL_SECONDS", 0.001):
        result = await remediate_node(_state())

    assert result["success"] is False
    assert result["timed_out"] is True


@pytest.mark.asyncio
async def test_launch_exception_returns_failure():
    with _patch_launch_error(RuntimeError("network error")):
        result = await remediate_node(_state())

    assert result["success"] is False
    assert "network error" in result["output_summary"]
