"""Remediate node — launches and polls an AAP job via the AAP controller API.

Same pattern as agent-service nodes/remediate.py (Workflow 1) but adapted
for RAN anomaly context. decide_node already resolved the template name, so
this calls AAP directly instead of going through an MCP tool layer.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from loguru import logger
from ran_remediation_service.aap_client import (
    get_job_output,
    get_job_status,
    launch_job,
)
from ran_remediation_service.config import (
    JOB_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    TERMINAL_STATUSES,
    now_iso,
)
from ran_remediation_service.models import RemediationState


async def remediate_node(state: RemediationState) -> dict:
    """Launch an AAP job for the decided template and wait for it to complete."""
    logger.info("Remediate node invoked incident_id={} template={}", state.incident_id, state.template_name)

    try:
        job_id = await launch_job(state.template_name, state.extra_vars)
    except Exception as exc:
        logger.exception("Failed to launch AAP job for template '{}'", state.template_name)
        return _failure(_error_reason(exc), stage="aap_launch")

    logger.info("Launched AAP job {} template='{}' incident_id={}", job_id, state.template_name, state.incident_id)

    try:
        status = await _poll_job(job_id)
    except Exception as exc:
        logger.exception("Failed to poll AAP job {}", job_id)
        return _failure(_error_reason(exc), stage="aap_poll", job_id=str(job_id))
    if status is None:
        return _failure(
            f"Job {job_id} timed out after {JOB_TIMEOUT_SECONDS}s",
            stage="aap_timeout",
            job_id=str(job_id),
            timed_out=True,
        )

    output = await _get_output(job_id)
    elapsed = status.get("elapsed", 0)
    finished = status.get("finished") or now_iso()

    if status.get("failed") or status.get("status") != "successful":
        traceback = status.get("result_traceback", "")
        job_status = status.get("status") or "failed"
        if status.get("failed") and job_status == "successful":
            job_status = "failed"
        failure = _failure(
            traceback or f"Job {job_id} finished with status {job_status}",
            stage="aap_job",
            job_id=str(job_id),
            job_status=job_status,
            elapsed=elapsed,
            timestamp=finished,
        )
        failure["output_summary"] = (traceback or output or failure["failure_reason"])[:1000]
        return failure

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
            status = await get_job_status(job_id)
        except Exception:
            logger.exception("Failed to poll job status job_id={}", job_id)
            raise
        if status.get("status") in TERMINAL_STATUSES:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    return None


async def _get_output(job_id: int) -> str:
    try:
        return await get_job_output(job_id)
    except Exception:
        logger.exception("Failed to get job output job_id={}", job_id)
        return ""


def _failure(
    error: str,
    *,
    stage: str,
    job_id: str = "",
    job_status: str = "failed",
    elapsed: float = 0,
    timestamp: str | None = None,
    timed_out: bool = False,
) -> dict:
    return {
        "job_id": job_id,
        "job_status": "timed_out" if timed_out else job_status,
        "success": False,
        "timed_out": timed_out,
        "output_summary": error[:1000],
        "failure_stage": stage,
        "failure_reason": (error.splitlines()[0].strip() or "AAP returned no failure detail")[:200],
        "timestamp": timestamp or now_iso(),
        "total_duration_ms": float(elapsed) * 1000,
    }


def _error_reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"AAP request failed (HTTP {exc.response.status_code})"
    return str(exc)
