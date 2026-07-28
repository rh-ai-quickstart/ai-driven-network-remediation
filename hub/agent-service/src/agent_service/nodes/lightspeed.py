import asyncio
import re
import time

import httpx
import yaml
from loguru import logger

from agent_service.config import (
    AAP_LIGHTSPEED_TEMPLATE,
    GITEA_PROJECT_NAME,
    LIGHTSPEED_PROMPT_TEMPLATE,
    LIGHTSPEED_SKIP_AAP,
    LIGHTSPEED_TIMEOUT_SECONDS,
    LIGHTSPEED_TOKEN,
    LIGHTSPEED_URL,
    LIGHTSPEED_VERIFY_SSL,
    now_iso,
)
from agent_service.models import RemediationResult
from agent_service.nodes.rag_retrieval import store_generated_playbook
from agent_service.utils import build_launch_extra_vars, invoke_tool as _invoke_tool

# Strip markdown code fences (``` or ```yaml/```yml) from LLM responses
_FENCE_RE = re.compile(r"```\w*\s*\n?", re.IGNORECASE)


_als_client: httpx.AsyncClient | None = None
_background_tasks: set[asyncio.Task] = set()


def _get_als_client() -> httpx.AsyncClient:
    global _als_client
    if _als_client is None:
        headers: dict[str, str] = {}
        if LIGHTSPEED_TOKEN:
            headers["Authorization"] = f"Bearer {LIGHTSPEED_TOKEN}"
        _als_client = httpx.AsyncClient(
            base_url=LIGHTSPEED_URL,
            timeout=LIGHTSPEED_TIMEOUT_SECONDS,
            headers=headers,
            verify=LIGHTSPEED_VERIFY_SSL,
        )
    return _als_client


def _build_playbook_name(rca, log_event) -> str:
    """Fallback name like 'remediate-cputhrottling-my-pod'."""
    failure = rca.failure_type.lower() if rca else "unknown"
    # Pick the most specific scope available
    if log_event and log_event.pod_name:
        scope = log_event.pod_name
    elif log_event and log_event.namespace:
        scope = log_event.namespace
    elif log_event and log_event.edge_site_id:
        scope = log_event.edge_site_id
    else:
        scope = "cluster"
    slug = re.sub(r"[^a-z0-9]+", "-", f"remediate-{failure}-{scope}").strip("-")
    return slug


def _extract_yaml(text: str) -> tuple[str, list | dict | None]:
    """Strip markdown fences and parse YAML once. Returns (cleaned_text, parsed)."""
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        parsed = yaml.safe_load(cleaned)
        return cleaned, parsed
    except yaml.YAMLError:
        return cleaned, None


def _playbook_name_from_parsed(parsed, rca, log_event) -> str:
    """Extract the play name from already-parsed YAML and slugify it."""
    if isinstance(parsed, list) and parsed:
        name = parsed[0].get("name", "") if isinstance(parsed[0], dict) else ""
        if name:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if slug:
                return slug
    return _build_playbook_name(rca, log_event)


def _build_prompt(rca, log_event) -> str:
    """Fill the ALS prompt template with RCA + log context."""
    return LIGHTSPEED_PROMPT_TEMPLATE.format(
        failure_type=rca.failure_type if rca else "Unknown",
        severity=rca.estimated_severity if rca else "unknown",
        namespace=log_event.namespace if log_event else "unknown",
        pod_name=log_event.pod_name if log_event else "unknown",
        summary=rca.summary if rca else "",
        evidence="\n".join(rca.evidence) if rca and rca.evidence else "N/A",
        recommended_actions=(", ".join(rca.recommended_actions) if rca else ""),
    )


def _build_attachments(rca, log_event) -> list[dict]:
    return [
        a
        for a in (
            (
                {"attachment_type": "log", "content_type": "text/plain", "content": log_event.raw}
                if log_event and log_event.raw
                else None
            ),
            (
                {"attachment_type": "configuration", "content_type": "text/plain", "content": "\n".join(rca.evidence)}
                if rca and rca.evidence
                else None
            ),
        )
        if a
    ]


def _log_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error(f"Background playbook storage failed: {task.exception()}")


async def drain_background_tasks(timeout_seconds: float = 10.0) -> None:
    if not _background_tasks:
        return
    logger.info(f"Draining {len(_background_tasks)} background task(s)")
    done, pending = await asyncio.wait(_background_tasks, timeout=timeout_seconds)
    for task in pending:
        task.cancel()
    if pending:
        logger.warning(f"{len(pending)} background task(s) cancelled on shutdown")


async def _call_als(prompt: str, attachments: list[dict]) -> dict:
    resp = await _get_als_client().post(
        "/v1/query",
        json={"query": prompt, "attachments": attachments},
    )
    resp.raise_for_status()
    return resp.json()


async def _upsert_template(name: str, playbook_path: str) -> dict:
    if not playbook_path:
        raise ValueError(f"playbook_path is required for template '{name}'")
    return await _invoke_tool(
        "upsert_job_template",
        {
            "template_name": name,
            "playbook": playbook_path,
            "base_template_name": AAP_LIGHTSPEED_TEMPLATE,
            "project_name": GITEA_PROJECT_NAME,
        },
    )


# TODO: remove stub once LIGHTSPEED_URL is always set in deployment.
# Without it, the decide node can route here when no ALS is configured,
# producing a confusing httpx error instead of a clean pass-through.
def _stub_result() -> dict:
    result = RemediationResult(
        action_taken="generate-playbook",
        tool_used="lightspeed",
        success=True,
        job_id="lightspeed-disabled",
        duration_seconds=0.0,
        output_summary="Lightspeed not configured (LIGHTSPEED_URL is empty)",
        timestamp=now_iso(),
    )
    return {"decision": "lightspeed", "remediation_result": result}


async def lightspeed_node(state) -> dict:
    """Ask Ansible Lightspeed to generate an Ansible playbook from RCA."""
    logger.info("Lightspeed node invoked")

    if not LIGHTSPEED_URL:
        logger.warning("LIGHTSPEED_URL not set, returning stub result")
        return _stub_result()

    rca = state.root_cause_analysis
    log_event = state.log_event

    t0 = time.monotonic()
    try:
        prompt = _build_prompt(rca, log_event)
        attachments = _build_attachments(rca, log_event)
        logger.debug(f"ALS prompt: {prompt}")
        logger.info(f"ALS attachments count: {len(attachments)}")

        data = await _call_als(prompt, attachments)
        duration = time.monotonic() - t0
        logger.debug(f"Raw ALS response: {data}")

        playbook_yaml, parsed = _extract_yaml(data.get("response", ""))
        playbook_name = _playbook_name_from_parsed(parsed, rca, log_event)

        logger.info(f"ALS responded in {duration:.2f}s, conversation_id={data.get('conversation_id', '')}")
        logger.debug(f"Generated playbook '{playbook_name}':\n{playbook_yaml}")

        result = RemediationResult(
            action_taken="generate-playbook",
            tool_used="lightspeed",
            success=True,
            job_id=data.get("conversation_id", ""),
            duration_seconds=round(duration, 2),
            output_summary=f"Generated playbook: {playbook_name}",
            timestamp=now_iso(),
            generated_template_name=playbook_name,
            generated_template_id=data.get("conversation_id", ""),
            generated_playbook_name=playbook_name,
            generated_playbook_preview=playbook_yaml,
        )
    except Exception:
        duration = time.monotonic() - t0
        logger.exception(f"Lightspeed call failed after {duration:.2f}s")
        result = RemediationResult(
            action_taken="generate-playbook",
            tool_used="lightspeed",
            success=False,
            job_id="",
            duration_seconds=round(duration, 2),
            output_summary="Lightspeed playbook generation failed",
            timestamp=now_iso(),
        )
        return {"decision": "lightspeed", "remediation_result": result}

    try:
        if LIGHTSPEED_SKIP_AAP:
            logger.info(f"LIGHTSPEED_SKIP_AAP=true, skipping AAP execution for '{playbook_name}'")
        else:
            result = await _execute_in_aap(
                result,
                playbook_name,
                playbook_yaml,
                log_event,
            )
    except Exception as exc:
        logger.exception(f"AAP execution failed for playbook '{playbook_name}'")
        result = result.model_copy(
            update={
                "success": False,
                "output_summary": f"AAP execution failed for {playbook_name}: {exc}"[:1000],
                "timestamp": now_iso(),
            }
        )

    if result.success:
        # TODO: raise or log an error when rca is None instead of falling back to defaults
        failure_type = rca.failure_type if rca else "Unknown"
        summary = rca.summary if rca else ""
        # Scheduled on the event loop; the asyncio scheduler will execute it.
        task = asyncio.create_task(store_generated_playbook(playbook_name, playbook_yaml, failure_type, summary))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        task.add_done_callback(_log_task_exception)

    return {"decision": "lightspeed", "remediation_result": result}


def _aap_step_failed(
    result: RemediationResult,
    response: dict,
    step_name: str,
) -> RemediationResult:
    error = response.get("error", f"{step_name} failed")
    logger.warning(f"{step_name} failed: {error}")
    return result.model_copy(
        update={"success": False, "output_summary": error[:1000], "timestamp": now_iso()},
    )


_SYNC_POLL_INTERVAL = 2
_SYNC_POLL_TIMEOUT = 60


async def _await_project_sync(update_id: int) -> bool:
    deadline = time.monotonic() + _SYNC_POLL_TIMEOUT
    while time.monotonic() < deadline:
        status = await _invoke_tool(
            "get_project_update_status", {"update_id": update_id},
        )
        sync_status = status.get("status", "")
        if sync_status == "successful":
            return True
        if sync_status in ("failed", "error", "canceled"):
            logger.warning(f"Project sync {update_id} ended with status: {sync_status}")
            return False
        await asyncio.sleep(_SYNC_POLL_INTERVAL)
    logger.warning(f"Project sync {update_id} timed out after {_SYNC_POLL_TIMEOUT}s")
    return False


async def _execute_in_aap(
    result: RemediationResult,
    name: str,
    yaml_content: str,
    log_event,
) -> RemediationResult:
    # Push playbook to Gitea
    commit = await _invoke_tool(
        "commit_playbook",
        {"playbook_name": name, "playbook_content": yaml_content},
    )
    if not commit.get("success"):
        return _aap_step_failed(result, commit, "commit_playbook")

    # Sync AAP project to pick up the new commit
    sync = await _invoke_tool("sync_project", {"project_name": GITEA_PROJECT_NAME})
    if not sync.get("success"):
        return _aap_step_failed(result, sync, "sync_project")

    update_id = sync.get("update_id")
    if update_id is not None:
        sync_ok = await _await_project_sync(update_id)
        if not sync_ok:
            return _aap_step_failed(
                result,
                {"error": f"Project sync timed out (update_id={update_id})"},
                "sync_project",
            )

    # Create or update the job template
    upsert = await _upsert_template(name, playbook_path=commit.get("file_path", ""))
    if not upsert.get("success"):
        return _aap_step_failed(result, upsert, "upsert_job_template")

    # Launch the job with target-specific variables
    extra_vars = build_launch_extra_vars(log_event)
    launch = await _invoke_tool(
        "launch_job",
        {"job_template_name": name, "extra_vars": extra_vars},
    )
    if not launch.get("success"):
        return _aap_step_failed(result, launch, "launch_job")

    job_id = str(launch.get("job_id", ""))
    return result.model_copy(
        update={
            "job_id": job_id,
            "output_summary": f"Launched AAP job {job_id} for {name} (pending)",
            "timestamp": now_iso(),
        },
    )
