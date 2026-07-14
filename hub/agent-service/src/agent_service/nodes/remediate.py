import asyncio
import time

from loguru import logger

from agent_service.config import (
    POLL_INTERVAL_SECONDS,
    TERMINAL_STATUSES,
    now_iso,
)
from agent_service.models import GraphConfig, RemediationResult
from agent_service.utils import invoke_tool as _invoke_tool

_TEMPLATE_KEYWORDS: dict[str, str] = {
    "nginx": "restart-nginx",
    "restart": "restart-nginx",
    "configuration": "restart-nginx",
    "crashloop": "restart-nginx",
    "config": "restart-nginx",
    "service": "restart-nginx",
    "scale": "scale-up-workers",
    "replica": "scale-up-workers",
    "oom": "scale-up-workers",
    "memory": "scale-up-workers",
    "disk": "clear-disk-space",
    "storage": "clear-disk-space",
}

_FAILURE_TYPE_DEFAULTS: dict[str, str] = {
    "CrashLoopBackOff": "restart-nginx",
    "ConfigError": "restart-nginx",
    "OOMKilled": "scale-up-workers",
    "StorageFull": "clear-disk-space",
    "NetworkTimeout": "restart-nginx",
}


def _resolve_template(action: str, failure_type: str | None = None) -> str:
    """Map a natural-language recommendation to the closest AAP job template."""
    lower = action.lower()
    for keyword, template in _TEMPLATE_KEYWORDS.items():
        if keyword in lower:
            return template
    if failure_type and failure_type in _FAILURE_TYPE_DEFAULTS:
        return _FAILURE_TYPE_DEFAULTS[failure_type]
    return action


async def _launch_job(template: str, log_event) -> dict:
    """Launch an AAP job template with context from the log event."""
    extra_vars = {
        "namespace": log_event.namespace,
        "pod_name": log_event.pod_name,
        "container": log_event.container,
        "edge_site_id": log_event.edge_site_id,
    }
    return await _invoke_tool(
        "launch_job",
        {"job_template_name": template, "extra_vars": extra_vars},
    )


async def _handle_completion(template: str, job_id: int, state, config):
    """Poll a launched job and return the appropriate state update."""
    status = await _poll_job(job_id, config.job_timeout)

    if status is None or status.get("status") not in TERMINAL_STATUSES:
        return _failure(
            state,
            config,
            template,
            f"Job {job_id} timed out",
            job_id,
            elapsed=config.job_timeout,
            timed_out=True,
        )

    output_text = await _get_output(job_id)
    elapsed = status.get("elapsed", 0)
    finished = status.get("finished") or now_iso()

    if status.get("failed"):
        traceback = status.get("result_traceback", "")
        summary = traceback or output_text
        return _failure(
            state,
            config,
            template,
            summary[:500],
            job_id,
            elapsed=elapsed,
            timestamp=finished,
        )

    return {
        "should_retry": False,
        "remediation_result": RemediationResult(
            action_taken=template,
            tool_used="aap",
            success=True,
            job_id=str(job_id),
            duration_seconds=float(elapsed),
            output_summary=output_text[:1000],
            timestamp=finished,
        ),
    }


def make_remediate_node(config: GraphConfig):
    """Factory: returns an async node that runs an AAP remediation job."""

    async def remediate_node(state) -> dict:
        logger.info("Remediate node invoked")
        rca = state.root_cause_analysis

        raw_action = rca.recommended_actions[0] if rca.recommended_actions else None
        template = _resolve_template(raw_action, rca.failure_type) if raw_action else None
        if not template:
            logger.warning("No recommended actions in RCA")
            return {
                "should_retry": False,
                "remediation_result": RemediationResult(
                    action_taken="none",
                    tool_used="aap",
                    success=False,
                    job_id="",
                    duration_seconds=0,
                    output_summary="No recommended actions in RCA",
                    timestamp=now_iso(),
                ),
            }

        try:
            launch = await _launch_job(template, state.log_event)
        except Exception as exc:
            logger.exception("Failed to launch AAP job")
            return _failure(state, config, template, str(exc))

        if not launch.get("success"):
            error = launch.get("error", "Unknown launch error")
            logger.warning(f"AAP launch failed: {error}")
            return _failure(state, config, template, error)

        return await _handle_completion(
            template,
            launch["job_id"],
            state,
            config,
        )

    return remediate_node


async def _poll_job(job_id: int, timeout: float) -> dict | None:
    """Poll get_job_status until terminal or timeout. Returns None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = await _invoke_tool(
                "get_job_status",
                {"job_id": job_id},
            )
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


async def _get_output(job_id: int) -> str:
    try:
        result = await _invoke_tool(
            "get_job_output",
            {"job_id": job_id},
        )
        return result.get("output", "")
    except Exception:
        logger.exception("Failed to get job output")
        return ""


def _failure(
    state,
    config: GraphConfig,
    template: str,
    error: str,
    job_id=None,
    *,
    elapsed=0,
    timestamp=None,
    timed_out=False,
) -> dict:
    entry = {"action": "remediate", "template": template, "error": error[:500]}
    if job_id is not None:
        entry["job_id"] = job_id
    attempts = state.failed_attempts + [entry]
    return {
        "failed_attempts": attempts,
        "should_retry": len(attempts) <= config.max_retries,
        "remediation_result": RemediationResult(
            action_taken=template,
            tool_used="aap",
            success=False,
            timed_out=timed_out,
            job_id=str(job_id or ""),
            duration_seconds=float(elapsed),
            output_summary=error[:1000],
            timestamp=timestamp or now_iso(),
        ),
    }
