import asyncio
import time
from dataclasses import dataclass

from loguru import logger

from agent_service.config import POLL_INTERVAL_SECONDS, TERMINAL_STATUSES, now_iso
from agent_service.utils import invoke_tool as _invoke_tool


@dataclass
class JobOutcome:
    success: bool
    timed_out: bool
    elapsed: float
    timestamp: str
    output_summary: str
    output_text: str


async def poll_job(job_id, timeout: float) -> dict | None:
    """Poll get_job_status until terminal or timeout. Returns None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = await _invoke_tool("get_job_status", {"job_id": job_id})
        except Exception:
            logger.exception("Failed to poll job status")
            return None
        if status.get("status") in TERMINAL_STATUSES:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    return None


async def get_job_output(job_id) -> str:
    try:
        result = await _invoke_tool("get_job_output", {"job_id": job_id})
        return result.get("output", "")
    except Exception:
        logger.exception("Failed to get job output")
        return ""


async def evaluate_job(job_id, timeout: float) -> JobOutcome:
    """Poll a launched AAP job to terminal state and derive its real outcome."""
    status = await poll_job(job_id, timeout)

    if status is None or status.get("status") not in TERMINAL_STATUSES:
        return JobOutcome(
            success=False,
            timed_out=True,
            elapsed=timeout,
            timestamp=now_iso(),
            output_summary=f"Job {job_id} timed out",
            output_text="",
        )

    output_text = await get_job_output(job_id)
    elapsed = status.get("elapsed", 0)
    finished = status.get("finished") or now_iso()

    if status.get("failed"):
        summary = status.get("result_traceback", "") or output_text
        return JobOutcome(
            success=False,
            timed_out=False,
            elapsed=elapsed,
            timestamp=finished,
            output_summary=summary[:500],
            output_text=output_text,
        )

    return JobOutcome(
        success=True,
        timed_out=False,
        elapsed=elapsed,
        timestamp=finished,
        output_summary=output_text[:1000],
        output_text=output_text,
    )
