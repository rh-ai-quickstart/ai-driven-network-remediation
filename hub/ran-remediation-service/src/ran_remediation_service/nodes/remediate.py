"""Remediate node — launches and polls an AAP job via LlamaStack MCP tools.

Same pattern as agent-service nodes/remediate.py (Workflow 1) but adapted
for RAN anomaly context. Uses invoke_tool() to call the mcp-aap server through
LlamaStack's /v1/tool-runtime/invoke endpoint.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

from ran_remediation_service.config import (
    JOB_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    TERMINAL_STATUSES,
    now_iso,
)
from ran_remediation_service.mcp_client import invoke_tool
from ran_remediation_service.models import RemediationState


async def remediate_node(state: RemediationState) -> dict:
    """Launch an AAP job for the decided template and wait for it to complete."""
    logger.info("Remediate node invoked incident_id={} template={}", state.incident_id, state.template_name)

    try:
        launch = await invoke_tool(
            "launch_job",
            {
                "job_template_name": state.template_name,
                "extra_vars": state.extra_vars,
            },
        )
    except Exception as exc:
        logger.exception("Failed to launch AAP job for template '{}'", state.template_name)
        return _failure(state.template_name, str(exc))

    if not launch.get("success"):
        error = launch.get("error", "Unknown launch error")
        logger.warning("AAP launch failed: {}", error)
        return _failure(state.template_name, error)

    job_id = launch["job_id"]
    logger.info("Launched AAP job {} template='{}' incident_id={}", job_id, state.template_name, state.incident_id)

    status = await _poll_job(job_id)
    if status is None:
        return _failure(
            state.template_name,
            f"Job {job_id} timed out after {JOB_TIMEOUT_SECONDS}s",
            job_id=str(job_id),
            timed_out=True,
        )

    output = await _get_output(job_id)
    elapsed = status.get("elapsed", 0)
    finished = status.get("finished") or now_iso()

    if status.get("failed"):
        traceback = status.get("result_traceback", "")
        summary = traceback or output
        return _failure(
            state.template_name,
            summary[:500],
            job_id=str(job_id),
            elapsed=elapsed,
            timestamp=finished,
        )

    return {
        "job_id": str(job_id),
        "job_status": status.get("status", "successful"),
        "success": True,
        "timed_out": False,
        "output_summary": output[:1000],
        "timestamp": finished,
        "total_duration_ms": float(elapsed) * 1000,
    }


async def _poll_job(job_id: int) -> dict | None:
    """Poll get_job_status until terminal or timeout. Returns None on timeout."""
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            status = await invoke_tool("get_job_status", {"job_id": job_id})
        except Exception:
            logger.exception("Failed to poll job status job_id={}", job_id)
            return None
        if status.get("status") in TERMINAL_STATUSES:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    return None


async def _get_output(job_id: int) -> str:
    try:
        result = await invoke_tool("get_job_output", {"job_id": job_id})
        return result.get("output", "")
    except Exception:
        logger.exception("Failed to get job output job_id={}", job_id)
        return ""


def _failure(
    template: str,
    error: str,
    *,
    job_id: str = "",
    elapsed: float = 0,
    timestamp: str | None = None,
    timed_out: bool = False,
) -> dict:
    return {
        "job_id": job_id,
        "job_status": "timed_out" if timed_out else "failed",
        "success": False,
        "timed_out": timed_out,
        "output_summary": error[:1000],
        "timestamp": timestamp or now_iso(),
        "total_duration_ms": float(elapsed) * 1000,
    }
