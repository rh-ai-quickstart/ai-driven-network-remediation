import asyncio
import json
import re
import time

import httpx
import yaml
from loguru import logger

from agent_service.config import (
    AAP_LIGHTSPEED_TEMPLATE,
    AAP_PROJECT_SYNC_TIMEOUT_SECONDS,
    AAP_RESOURCE_PREFIX,
    GITEA_PROJECT_NAME,
    LIGHTSPEED_MAX_GENERATION_ATTEMPTS,
    LIGHTSPEED_PROMPT_TEMPLATE,
    LIGHTSPEED_SKIP_AAP,
    LIGHTSPEED_SYSTEM_PROMPT,
    LIGHTSPEED_TIMEOUT_SECONDS,
    LIGHTSPEED_TOKEN,
    LIGHTSPEED_URL,
    LIGHTSPEED_VERIFY_SSL,
    PLAYBOOK_VALIDATION_TIMEOUT_SECONDS,
    now_iso,
)
from agent_service.evidence import (
    get_pod_logs_for_attachment,
    get_structured_attachments,
)
from agent_service.models import GraphConfig, RemediationResult, SelectedAction
from agent_service.nodes._aap_job import evaluate_job
from agent_service.nodes.rag_retrieval import store_generated_playbook
from agent_service.playbook_sanitize import quote_jinja
from agent_service.target_resolution import resolve_target_with_lookup
from agent_service.utils import invoke_tool as _invoke_tool

# Strip markdown code fences (``` or ```yaml/```yml) from LLM responses
_FENCE_RE = re.compile(r"```\w*\s*\n?", re.IGNORECASE)
_MAX_ERROR_SUMMARY_CHARS = 1000
_MAX_REJECTED_RESPONSE_CHARS = 16000
_AAP_TOOL = "aap"
_PLAY_ACTION_SECTIONS = ("tasks", "pre_tasks", "post_tasks", "roles")


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
    """Parse valid YAML unchanged; try quoting bare Jinja only after a parse failure."""
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        parsed = yaml.safe_load(cleaned)
        return cleaned, parsed
    except yaml.YAMLError:
        cleaned = quote_jinja(cleaned)
        try:
            return cleaned, yaml.safe_load(cleaned)
        except yaml.YAMLError:
            return cleaned, None


def _playbook_name_from_parsed(parsed, rca, log_event) -> str:
    """Extract the play name from already-parsed YAML and slugify it."""
    if isinstance(parsed, list) and parsed:
        name = parsed[0].get("name", "") if isinstance(parsed[0], dict) else ""
        if isinstance(name, str) and name:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if slug:
                return slug
    return _build_playbook_name(rca, log_event)


_REQUEST_TEMPLATE = """GOAL
{goal}

TARGET
{target}

OBSERVED FACTS
{evidence}

DIAGNOSIS
{diagnosis}

PARAMETERS
{parameters}

ANSIBLE EXTRA VARS (each key is a top-level variable)
{launch_vars}

EXECUTION CONSTRAINTS
{instructions}

OUTPUT
Return the complete Ansible playbook as a YAML list, without prose or code fences.
Choose appropriate Ansible modules and tasks to implement only the requested change.
"""


def _launch_vars(action: SelectedAction) -> dict:
    target = action.target
    launch_vars = {
        "edge_site_id": target.edge_site_id,
        "namespace": target.namespace,
        "resource_kind": target.kind,
        "resource_name": target.name,
        "container_name": target.container,
        "remediation_parameters": action.parameters,
    }
    if target.kind.casefold() == "deployment":
        launch_vars.update(deployment_name=target.name, deployment=target.name)
    return launch_vars


def _build_prompt(rca, action: SelectedAction) -> str:
    """Pass the selected change to ALS without post-remediation checks."""
    diagnosis = {"summary": rca.summary, "confidence": rca.confidence, "runbook_reference": rca.runbook_reference}
    return _REQUEST_TEMPLATE.format(
        goal=" ".join(action.goal.split()),
        target=json.dumps(action.target.model_dump()),
        evidence=json.dumps(action.evidence),
        diagnosis=json.dumps(diagnosis),
        parameters=json.dumps(action.parameters),
        launch_vars=json.dumps(_launch_vars(action)),
        instructions=LIGHTSPEED_PROMPT_TEMPLATE,
    )


_MAX_RAW_LOG_CHARS = 2000
_MAX_EVIDENCE_ITEMS = 5
_MAX_EVIDENCE_CHARS = 4000
_MAX_ATTACHMENT_CHARS = 6000


def _build_attachments(rca, log_event, pod_logs: str = "", extra_attachments: list[dict] | None = None) -> list[dict]:
    attachments = [
        attachment
        for attachment in (
            (
                {"attachment_type": "log", "content_type": "text/plain", "content": log_event.raw[-_MAX_RAW_LOG_CHARS:]}
                if log_event and log_event.raw
                else None
            ),
            ({"attachment_type": "log", "content_type": "text/plain", "content": pod_logs} if pod_logs else None),
            (
                {
                    "attachment_type": "configuration",
                    "content_type": "text/plain",
                    "content": "\n".join(rca.evidence[:_MAX_EVIDENCE_ITEMS])[:_MAX_EVIDENCE_CHARS],
                }
                if rca and rca.evidence
                else None
            ),
        )
        if attachment
    ]
    attachments.extend(_budget_attachment(attachment) for attachment in extra_attachments or [])
    return attachments


def _budget_attachment(attachment: dict) -> dict:
    if len(attachment.get("content", "")) <= _MAX_ATTACHMENT_CHARS:
        return attachment
    if attachment.get("content_type") == "application/json":
        content = json.dumps({"omitted": "Supplementary attachment exceeds budget"})
    else:
        content = attachment["content"][:_MAX_ATTACHMENT_CHARS] + "\n[Supplementary evidence omitted]"
    return {**attachment, "content": content}


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
    query = f"{LIGHTSPEED_SYSTEM_PROMPT}\n\n{prompt}" if LIGHTSPEED_SYSTEM_PROMPT else prompt
    payload = {"query": query, "attachments": attachments}
    logger.debug(f"ALS prompt: {query}")
    logger.info(f"ALS attachments count: {len(attachments)}")
    started = time.monotonic()
    resp = await _get_als_client().post("/v1/query", json=payload)
    resp.raise_for_status()
    als_response = resp.json()
    duration = time.monotonic() - started
    logger.info(f"ALS responded in {duration:.2f}s, conversation_id={als_response.get('conversation_id', '')}")
    return als_response


_CORRECTION_TEMPLATE = """{prompt}

Your previous response failed validation and was not executed.
Return a complete corrected YAML list of plays. Fix the reported YAML or Ansible syntax error.
Keep the original selected action, exact target and parameters. Do not add post-remediation checks.

Validation error: {error}

Rejected response (may be truncated):
{rejected}
"""


def _playbook_validation_error(parsed) -> str:
    """Check basic playbook structure, not the chosen remediation or its implementation."""
    if not isinstance(parsed, list) or not parsed:
        return "Expected a non-empty YAML list of plays"
    for play in parsed:
        if not isinstance(play, dict):
            return "Each play must be a YAML mapping"
        if play.get("import_playbook") or play.get("ansible.builtin.import_playbook"):
            continue
        if not play.get("hosts"):
            return "Each play must specify hosts"
        if not any(isinstance(play.get(section), list) and play[section] for section in _PLAY_ACTION_SECTIONS):
            return "Each play must contain tasks or roles"
    return ""


async def _generate_playbook(prompt: str, attachments: list[dict], extra_vars: dict) -> tuple[dict, list | None]:
    generation_prompt = prompt
    previous_error = ""
    for attempt in range(LIGHTSPEED_MAX_GENERATION_ATTEMPTS):
        als_response = await _call_als(generation_prompt, attachments)
        logger.debug(f"Raw ALS response: {als_response}")
        playbook_yaml, parsed = _extract_yaml(als_response.get("response", ""))
        try:
            validation_error = await _generation_validation_error(playbook_yaml, parsed, extra_vars)
        except Exception as exc:
            return {**als_response, "validation_error": f"Playbook validation unavailable: {exc}"}, None
        if not validation_error:
            return {**als_response, "response": playbook_yaml}, parsed
        parsed = None
        if validation_error == previous_error:
            break
        previous_error = validation_error
        if attempt + 1 < LIGHTSPEED_MAX_GENERATION_ATTEMPTS:
            logger.warning(f"Generated playbook validation failed: {validation_error}; requesting one correction")
            generation_prompt = _CORRECTION_TEMPLATE.format(
                prompt=prompt,
                error=validation_error[:_MAX_ERROR_SUMMARY_CHARS],
                rejected=playbook_yaml[:_MAX_REJECTED_RESPONSE_CHARS],
            )
    return {**als_response, "validation_error": validation_error}, parsed


async def _generation_validation_error(playbook_yaml: str, parsed, extra_vars: dict) -> str:
    if parsed is None:
        try:
            yaml.safe_load(playbook_yaml)
        except yaml.YAMLError as exc:
            return f"Invalid YAML: {exc}"
    structural_error = _playbook_validation_error(parsed)
    if structural_error:
        return structural_error
    validation = await _invoke_tool(
        "validate_playbook",
        {"playbook_content": playbook_yaml, "base_template_name": AAP_LIGHTSPEED_TEMPLATE, "extra_vars": extra_vars},
        timeout_seconds=PLAYBOOK_VALIDATION_TIMEOUT_SECONDS,
    )
    if validation.get("success") is not True or not isinstance(validation.get("valid"), bool):
        raise RuntimeError(validation.get("error") or "Validator returned an unsuccessful or malformed response")
    if validation["valid"]:
        return ""
    return str(validation.get("error") or "Ansible syntax validation failed")


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


def _failed_generation(message: str, started: float, conversation_id: str = "") -> RemediationResult:
    return RemediationResult(
        action_taken="generate-playbook",
        tool_used="lightspeed",
        success=False,
        job_id="",
        lightspeed_conversation_id=conversation_id or None,
        duration_seconds=round(time.monotonic() - started, 2),
        output_summary=message[:_MAX_ERROR_SUMMARY_CHARS],
        timestamp=now_iso(),
    )


def _generation_result(state, als_response: dict, parsed, started: float) -> RemediationResult:
    conversation_id = als_response.get("conversation_id", "")
    if not isinstance(parsed, list):
        error = als_response.get("validation_error", "Invalid YAML")
        logger.warning(f"Playbook validation failed, refusing to launch: {error}")
        explanation = als_response.get("response", "")
        message = f"Generated playbook failed validation: {error}"
        if explanation:
            message += f". ALS response: {explanation}"
        return _failed_generation(message, started, conversation_id)
    playbook_yaml = als_response["response"]
    name = AAP_RESOURCE_PREFIX + _playbook_name_from_parsed(parsed, state.root_cause_analysis, state.log_event)
    logger.debug(f"Generated playbook '{name}' passed syntax validation:\n{playbook_yaml}")
    return RemediationResult(
        action_taken="generate-playbook",
        tool_used="lightspeed",
        success=True,
        job_id="",
        lightspeed_conversation_id=conversation_id or None,
        duration_seconds=round(time.monotonic() - started, 2),
        output_summary=f"Generated playbook: {name}",
        timestamp=now_iso(),
        generated_template_name=name,
        generated_playbook_name=name,
        generated_playbook_preview=playbook_yaml,
    )


async def _generate_selected_playbook(state, action: SelectedAction, started: float) -> RemediationResult:
    prompt = _build_prompt(state.root_cause_analysis, action)
    attachments = _build_attachments(
        state.root_cause_analysis,
        state.log_event,
        pod_logs=get_pod_logs_for_attachment(state),
        extra_attachments=get_structured_attachments(state),
    )
    als_response, parsed = await _generate_playbook(prompt, attachments, _launch_vars(action))
    return _generation_result(state, als_response, parsed, started)


def _store_successful_playbook(outcome: RemediationResult, rca) -> None:
    if not outcome.success:
        return
    task = asyncio.create_task(
        store_generated_playbook(
            outcome.generated_playbook_name,
            outcome.generated_playbook_preview,
            rca.failure_type,
            rca.summary,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_task_exception)


async def lightspeed_node(state) -> dict:
    """Ask ALS to implement NOC's remediation goal, then execute through AAP."""
    logger.info("Lightspeed node invoked")
    started = time.monotonic()
    try:
        action = state.root_cause_analysis.selected_action if state.root_cause_analysis else None
        if action is None:
            raise ValueError("NOC did not select a remediation goal")
        action = await resolve_target_with_lookup(action, state.deployment_observations, state.log_event, _invoke_tool)
        action = action.model_copy(deep=True)
        if not LIGHTSPEED_URL:
            raise ValueError("Lightspeed is not configured")
        outcome = await _generate_selected_playbook(state, action, started)
    except Exception as exc:
        logger.exception(f"Lightspeed generation failed after {time.monotonic() - started:.2f}s")
        outcome = _failed_generation(f"Lightspeed playbook generation failed: {exc}", started)
        return {"decision": "lightspeed", "remediation_result": outcome}
    try:
        if outcome.success and not LIGHTSPEED_SKIP_AAP:
            outcome = await _execute_in_aap(outcome, action)
        elif outcome.success:
            logger.info(f"LIGHTSPEED_SKIP_AAP=true, skipping AAP execution for '{outcome.generated_playbook_name}'")
    except Exception as exc:
        logger.exception(f"AAP execution failed for playbook '{outcome.generated_playbook_name}'")
        outcome = _aap_step_failed(outcome, {"error": f"AAP execution failed: {exc}"}, "execution")
    _store_successful_playbook(outcome, state.root_cause_analysis)
    return {"decision": "lightspeed", "remediation_result": outcome}


def _aap_step_failed(
    result: RemediationResult,
    response: dict,
    step_name: str,
) -> RemediationResult:
    error = response.get("error")
    failure_summary = f"{step_name} failed" + (f": {error}" if error else "")
    logger.warning(failure_summary)
    return result.model_copy(
        update={
            "success": False,
            "output_summary": failure_summary[:_MAX_ERROR_SUMMARY_CHARS],
            "timestamp": now_iso(),
        },
    )


_SYNC_POLL_INTERVAL = 2
_SYNC_POLL_TIMEOUT = AAP_PROJECT_SYNC_TIMEOUT_SECONDS


async def _await_project_sync(update_id: int) -> bool:
    deadline = time.monotonic() + _SYNC_POLL_TIMEOUT
    while time.monotonic() < deadline:
        status = await _invoke_tool(
            "get_project_update_status",
            {"update_id": update_id},
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


async def _publish_in_aap(outcome: RemediationResult, name: str, yaml_content: str) -> RemediationResult | None:
    commit = await _invoke_tool("commit_playbook", {"playbook_name": name, "playbook_content": yaml_content})
    if not commit.get("success"):
        return _aap_step_failed(outcome, commit, "commit_playbook")
    logger.info(f"Playbook committed to Gitea: file_path={commit.get('file_path', '')}")
    sync = await _invoke_tool("sync_project", {"project_name": GITEA_PROJECT_NAME})
    if not sync.get("success"):
        return _aap_step_failed(outcome, sync, "sync_project")
    update_id = sync.get("update_id")
    if update_id is not None and not await _await_project_sync(update_id):
        return _aap_step_failed(outcome, {"error": f"Project sync timed out (update_id={update_id})"}, "sync_project")
    upsert = await _upsert_template(name, playbook_path=commit.get("file_path", ""))
    if not upsert.get("success"):
        return _aap_step_failed(outcome, upsert, "upsert_job_template")
    logger.info(f"Job template upserted: '{name}'")
    return None


async def _execute_in_aap(result: RemediationResult, action: SelectedAction) -> RemediationResult:
    name = result.generated_playbook_name
    logger.info(f"AAP execution pipeline started for playbook '{name}'")
    publication_failure = await _publish_in_aap(result, name, result.generated_playbook_preview)
    if publication_failure is not None:
        return publication_failure
    launch = await _invoke_tool("launch_job", {"job_template_name": name, "extra_vars": _launch_vars(action)})
    if not launch.get("success"):
        return _aap_step_failed(result, launch, "launch_job")

    launched_result = result.model_copy(update={"action_taken": name, "tool_used": _AAP_TOOL})
    return await _monitor_launched_job(launched_result, launch.get("job_id"))


async def _monitor_launched_job(result: RemediationResult, launched_job_id) -> RemediationResult:
    job_id = str(launched_job_id or "")
    logger.info(f"AAP job launched: job_id={job_id} template='{result.generated_template_name}'")

    try:
        outcome = await evaluate_job(launched_job_id, GraphConfig().job_timeout)
    except Exception as exc:
        return result.model_copy(
            update={
                "job_id": job_id,
                "success": False,
                "output_summary": f"AAP job {job_id} monitoring failed: {exc}"[:_MAX_ERROR_SUMMARY_CHARS],
                "timestamp": now_iso(),
            }
        )
    logger.info(
        f"AAP job {job_id} for '{result.generated_playbook_name}' finished: "
        f"success={outcome.success} timed_out={outcome.timed_out}"
    )
    return result.model_copy(
        update={
            "job_id": job_id,
            "success": outcome.success,
            "timed_out": outcome.timed_out,
            "output_summary": outcome.output_summary,
            "timestamp": outcome.timestamp,
        }
    )
