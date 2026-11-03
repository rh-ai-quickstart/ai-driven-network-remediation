import asyncio
import json
import time
from dataclasses import dataclass

from loguru import logger

from agent_service.config import POLL_INTERVAL_SECONDS, TERMINAL_STATUSES, now_iso
from agent_service.utils import invoke_tool as _invoke_tool

_FAILED_STATUSES = frozenset({"failed", "error", "canceled"})
_FAILURE_MARKERS = ("FAILED!", "UNREACHABLE!", "fatal:")
_MAX_SUMMARY_CHARS = 1000
_TASK_HEADER_PREFIX = "TASK ["
_NONZERO_RETURN_CODE = "non-zero return code"


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


def _task_name_before_failure(lines: list[str], failure_index: int) -> str:
    task_header = next(
        (line for line in reversed(lines[:failure_index]) if line.startswith(_TASK_HEADER_PREFIX)),
        "",
    )
    return task_header.removeprefix(_TASK_HEADER_PREFIX).partition("]")[0]


def _failure_message(failure_block: str) -> str:
    json_start = failure_block.find("{")
    if json_start < 0:
        return ""
    try:
        details, _ = json.JSONDecoder().raw_decode(failure_block[json_start:])
    except json.JSONDecodeError:
        return ""
    if not isinstance(details, dict):
        return ""
    message = details.get("msg", "")
    if not isinstance(message, str):
        return ""
    stderr = details.get("stderr", "")
    if _NONZERO_RETURN_CODE in message.lower() and isinstance(stderr, str) and stderr:
        return f"{message}: {stderr}"
    return message


def _failure_summary(output_text: str) -> str:
    lines = output_text.splitlines()
    failure_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if any(marker in lines[index] for marker in _FAILURE_MARKERS)
        ),
        -1,
    )
    if failure_index < 0:
        return ""

    task_name = _task_name_before_failure(lines, failure_index)
    failure_end = next(
        (index for index in range(failure_index + 1, len(lines)) if lines[index].startswith(_TASK_HEADER_PREFIX)),
        len(lines),
    )
    failure_block = "\n".join(lines[failure_index:failure_end])
    message = _failure_message(failure_block) or failure_block
    if task_name and message:
        return f"Task '{task_name}' failed: {message}"
    return message


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
    job_status = status.get("status") or "failed"

    if status.get("failed") or job_status in _FAILED_STATUSES:
        traceback = status.get("result_traceback", "")
        summary = (
            _failure_summary(output_text)
            or _failure_summary(traceback)
            or traceback
            or output_text
            or f"Job {job_id} {job_status}"
        )
        return JobOutcome(
            success=False,
            timed_out=False,
            elapsed=elapsed,
            timestamp=finished,
            output_summary=summary[:_MAX_SUMMARY_CHARS],
            output_text=output_text,
        )

    return JobOutcome(
        success=True,
        timed_out=False,
        elapsed=elapsed,
        timestamp=finished,
        output_summary=output_text[:_MAX_SUMMARY_CHARS],
        output_text=output_text,
    )
