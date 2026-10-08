import re

from loguru import logger

from agent_service.config import (
    FAST_PATH_LAST_HEAL_ANNOTATION,
    now_iso,
)
from agent_service.edge_site import remediation_should_retry, resolve_edge_site_id
from agent_service.fast_path import recent_deployment_remediation_actuation, target_deployment_name
from agent_service.models import GraphConfig, RemediationResult
from agent_service.nodes._aap_job import evaluate_job
from agent_service.utils import build_launch_extra_vars
from agent_service.utils import invoke_tool as _invoke_tool

# Demo-only: these templates must be pre-created in AAP manually.
# Keep only unambiguous keyword mappings; catch-all words (restart, config,
# service, ...) matched almost every RCA action and collapsed selection onto
# restart-nginx. Ambiguous cases now resolve to None and are handled upstream.
_TEMPLATE_KEYWORDS: dict[str, str] = {
    "nginx": "restart-nginx",
    "scale": "scale-up-workers",
    "replica": "scale-up-workers",
    "oom": "scale-up-workers",
    "memory": "scale-up-workers",
    "disk": "clear-disk-space",
    "storage": "clear-disk-space",
}

# Only failure types that genuinely map to a single honest playbook.
_FAILURE_TYPE_DEFAULTS: dict[str, str] = {
    "CrashLoopBackOff": "restart-nginx",
    "OOMKilled": "scale-up-workers",
    "StorageFull": "clear-disk-space",
}


def _keyword_template(action: str) -> str | None:
    """Match keywords on word boundaries so substrings (oom in room) don't hit."""
    lower = action.lower()
    for keyword, template in _TEMPLATE_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}", lower):
            return template
    return None


def _resolve_template(action: str, failure_type: str | None = None) -> str | None:
    """Map a natural-language recommendation to the closest AAP job template.

    The structured failure_type is a more reliable signal than free-text keyword
    matching, so its curated default wins whenever it applies (even if a keyword
    in the action text points elsewhere). Keyword matching only resolves cases
    the failure_type cannot. Returns None when neither applies, so callers
    escalate instead of fabricating a template name for AAP.
    """
    if failure_type and failure_type in _FAILURE_TYPE_DEFAULTS:
        return _FAILURE_TYPE_DEFAULTS[failure_type]
    return _keyword_template(action)


async def _launch_job(template: str, log_event, edge_site_id: str) -> dict:
    """Launch an AAP job template with context from the log event."""
    extra_vars = build_launch_extra_vars(log_event)
    if edge_site_id and edge_site_id != "unknown":
        extra_vars["edge_site_id"] = edge_site_id
    return await _invoke_tool(
        "launch_job",
        {
            "job_template_name": template,
            "extra_vars": extra_vars,
        },
    )


async def _handle_completion(template: str, job_id: int, state, config, *, edge_site_id: str = ""):
    """Poll a launched job and return the appropriate state update."""
    outcome = await evaluate_job(job_id, config.job_timeout)

    if not outcome.success:
        return _failure(
            state,
            config,
            template,
            outcome.output_summary,
            job_id,
            elapsed=outcome.elapsed,
            timestamp=outcome.timestamp,
            timed_out=outcome.timed_out,
            edge_site_id=edge_site_id,
        )

    return {
        "should_retry": False,
        "remediation_result": RemediationResult(
            action_taken=template,
            tool_used="aap",
            success=True,
            job_id=str(job_id),
            duration_seconds=float(outcome.elapsed),
            output_summary=outcome.output_summary,
            timestamp=outcome.timestamp,
        ),
    }


def make_remediate_node(config: GraphConfig):
    """Factory: returns an async node that runs an AAP remediation job."""

    async def remediate_node(state) -> dict:
        logger.info("Remediate node invoked")
        rca = state.root_cause_analysis

        raw_action = rca.recommended_actions[0] if rca.recommended_actions else None
        template = state.selected_template or (
            _resolve_template(raw_action, rca.failure_type) if raw_action else None
        )
        if not template:
            logger.warning("No matching remediation template for RCA")
            return {
                "should_retry": False,
                "remediation_result": RemediationResult(
                    action_taken="none",
                    tool_used="aap",
                    success=False,
                    job_id="",
                    duration_seconds=0,
                    output_summary="No matching remediation template for RCA",
                    timestamp=now_iso(),
                ),
            }

        log_event = state.log_event
        edge_site_id = resolve_edge_site_id(
            log_event,
            resource_specs=state.resource_specs or "",
            raw_event=state.raw_event or "",
        )
        raw_event = state.raw_event or ""
        if log_event:
            deployment = target_deployment_name(log_event.pod_name, log_event.namespace)
            actuation = (
                await recent_deployment_remediation_actuation(
                    namespace=log_event.namespace,
                    deployment=deployment,
                    edge_site_id=edge_site_id,
                    raw_event=raw_event,
                )
                if deployment
                else None
            )
            if actuation:
                if actuation == "spoke":
                    summary = (
                        f"Deployment {deployment} was already remediated on the spoke "
                        f"({FAST_PATH_LAST_HEAL_ANNOTATION}); skipping duplicate hub AAP job"
                    )
                else:
                    summary = (
                        f"Deployment {deployment} had a recent hub rollout restart; "
                        f"skipping duplicate AAP job"
                    )
                logger.info(summary)
                return {
                    "should_retry": False,
                    "fast_path_actuation": actuation,
                    "remediation_result": RemediationResult(
                        action_taken="fast_path_skip",
                        tool_used=actuation,
                        success=True,
                        job_id="",
                        duration_seconds=0,
                        output_summary=summary,
                        timestamp=now_iso(),
                    ),
                }

        try:
            launch = await _launch_job(template, state.log_event, edge_site_id)
        except Exception as exc:
            logger.exception("Failed to launch AAP job")
            return _failure(state, config, template, str(exc), edge_site_id=edge_site_id)

        if not launch.get("success"):
            error = launch.get("error", "Unknown launch error")
            logger.warning(f"AAP launch failed: {error}")
            return _failure(state, config, template, error, edge_site_id=edge_site_id)

        return await _handle_completion(
            template,
            launch["job_id"],
            state,
            config,
            edge_site_id=edge_site_id,
        )

    return remediate_node


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
    edge_site_id: str = "",
) -> dict:
    entry = {"action": "remediate", "template": template, "error": error[:500]}
    if job_id is not None:
        entry["job_id"] = job_id
    attempts = state.failed_attempts + [entry]
    can_retry = job_id is None and remediation_should_retry(
        error,
        edge_site_id,
        len(attempts),
        config.max_retries,
    )
    return {
        "failed_attempts": attempts,
        "should_retry": can_retry,
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
