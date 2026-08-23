import asyncio
import json
import re
import time

import httpx
import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from agent_service.config import (
    AAP_LIGHTSPEED_TEMPLATE,
    GITEA_PROJECT_NAME,
    LIGHTSPEED_MAX_SUMMARIZE_CHARS,
    LIGHTSPEED_PROMPT_TEMPLATE,
    LIGHTSPEED_SKIP_AAP,
    LIGHTSPEED_SUMMARIZE_PROMPT,
    LIGHTSPEED_SYSTEM_PROMPT,
    LIGHTSPEED_TIMEOUT_SECONDS,
    LIGHTSPEED_TOKEN,
    LIGHTSPEED_URL,
    LIGHTSPEED_VERIFY_SSL,
    get_llm,
    now_iso,
)
from agent_service.evidence import build_evidence_prompt, build_grounding_text, get_pod_logs_for_attachment, get_structured_attachments
from agent_service.models import RemediationResult
from agent_service.nodes.rag_retrieval import store_generated_playbook
from agent_service.playbook_sanitize import _quote_jinja, fix_ansible_facts, sanitize_playbook
from agent_service.utils import _normalize_component_name, build_launch_extra_vars
from agent_service.utils import invoke_tool as _invoke_tool

# Strip markdown code fences (``` or ```yaml/```yml) from LLM responses
_FENCE_RE = re.compile(r"```\w*\s*\n?", re.IGNORECASE)
_SUMMARY_KEYS = ("affected_component", "root_cause", "remediation_steps", "edge_site_id")


def _extract_json_object(text: str) -> str | None:
    """Find the outermost balanced {...} in text by scanning from the last }."""
    end = text.rfind("}")
    if end < 0:
        return None
    depth = 0
    for i in range(end, -1, -1):
        if text[i] == "}":
            depth += 1
        elif text[i] == "{":
            depth -= 1
        if depth == 0:
            return text[i : end + 1]
    return None


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
    cleaned = _quote_jinja(cleaned)
    cleaned = fix_ansible_facts(cleaned)
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


def _parse_summary_json(text: str) -> dict:
    """Parse a summarizer response into the overlay keys we accept."""
    cleaned = _FENCE_RE.sub("", text).strip()
    candidates = [cleaned]
    extracted = _extract_json_object(cleaned)
    if extracted and extracted not in candidates:
        candidates.append(extracted)
    parsed = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(parsed, dict):
        return {}
    return {
        key: parsed[key]
        for key in _SUMMARY_KEYS
        if isinstance(parsed.get(key), str) and parsed[key]
    }


async def _summarize_evidence(rca, investigation_evidence: str) -> dict:
    """Use the LLM to synthesize RCA + evidence into targeted remediation fields."""
    prompt = LIGHTSPEED_SUMMARIZE_PROMPT.format(
        rca_summary=rca.summary if rca else "",
        rca_evidence="\n".join(rca.evidence) if rca and rca.evidence else "",
        recommended_actions=", ".join(rca.recommended_actions) if rca else "",
        investigation_evidence=investigation_evidence[:LIGHTSPEED_MAX_SUMMARIZE_CHARS],
    )
    messages = [
        SystemMessage(content="Respond ONLY with valid JSON, no extra text."),
        HumanMessage(content=prompt),
    ]
    response = await get_llm().ainvoke(messages)
    text = response.content.strip()
    parsed = _parse_summary_json(text)
    if not parsed and text:
        logger.warning(f"Summarize response was not valid JSON: {text[:200]}")
    return parsed


def _build_prompt(
    rca,
    log_event,
    llm_summary: dict | None = None,
    extra_evidence: str = "",
) -> str:
    """Build the ALS prompt, omitting fields that have no useful value."""
    summary = rca.summary if rca else ""
    target_component = log_event.pod_name if log_event else "unknown"
    evidence_str = "\n".join(rca.evidence) if rca and rca.evidence else ""
    actions = ", ".join(rca.recommended_actions) if rca else ""

    if llm_summary:
        if llm_summary.get("affected_component"):
            target_component = llm_summary["affected_component"]
        if llm_summary.get("root_cause"):
            summary = llm_summary["root_cause"]
        if llm_summary.get("remediation_steps"):
            actions = llm_summary["remediation_steps"]

    if extra_evidence:
        evidence_str = (evidence_str + "\n\n" + extra_evidence) if evidence_str else extra_evidence

    fields = [
        ("Failure type", rca.failure_type if rca else "Unknown"),
        ("Severity", rca.estimated_severity if rca else "unknown"),
        ("Edge site cluster", log_event.edge_site_id if log_event else ""),
        ("Namespace", log_event.namespace if log_event else "unknown"),
        ("Target component", target_component),
        ("Summary", summary),
    ]

    lines = ["Generate an Ansible playbook to remediate this OpenShift cluster issue.\n"]
    for label, value in fields:
        if value:
            lines.append(f"{label}: {value}")

    if evidence_str:
        lines.append(f"\nEvidence:\n{evidence_str}")

    if actions:
        lines.append(f"\nRecommended actions: {actions}")

    lines.append("")
    lines.append(LIGHTSPEED_PROMPT_TEMPLATE)

    return "\n".join(lines)


_MAX_RAW_LOG_CHARS = 2000
_MAX_EVIDENCE_ITEMS = 5
_MAX_EVIDENCE_CHARS = 4000
_MAX_ATTACHMENT_CHARS = 6000


def _build_attachments(rca, log_event, pod_logs: str = "", extra_attachments: list[dict] | None = None) -> list[dict]:
    result = [
        a
        for a in (
            (
                {"attachment_type": "log", "content_type": "text/plain", "content": log_event.raw[-_MAX_RAW_LOG_CHARS:]}
                if log_event and log_event.raw
                else None
            ),
            (
                {"attachment_type": "log", "content_type": "text/plain", "content": pod_logs}
                if pod_logs
                else None
            ),
            (
                {"attachment_type": "configuration", "content_type": "text/plain", "content": "\n".join(rca.evidence[:_MAX_EVIDENCE_ITEMS])[:_MAX_EVIDENCE_CHARS]}
                if rca and rca.evidence
                else None
            ),
        )
        if a
    ]
    if extra_attachments:
        for att in extra_attachments:
            if len(att.get("content", "")) > _MAX_ATTACHMENT_CHARS:
                att = {**att, "content": att["content"][:_MAX_ATTACHMENT_CHARS]}
            result.append(att)
    return result


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
    resp = await _get_als_client().post("/v1/query", json=payload)
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
        investigation_evidence = build_evidence_prompt(state)
        grounding_text = build_grounding_text(state)
        llm_summary = None
        if grounding_text:
            try:
                llm_summary = await _summarize_evidence(rca, grounding_text)
                logger.info(f"LLM remediation summary: {llm_summary}")
            except Exception:
                logger.opt(exception=True).warning("LLM summarization failed, falling back to template-only prompt")

        prompt = _build_prompt(rca, log_event, llm_summary, investigation_evidence)
        pod_logs = get_pod_logs_for_attachment(state)
        structured = get_structured_attachments(state)
        attachments = _build_attachments(rca, log_event, pod_logs=pod_logs, extra_attachments=structured)
        logger.debug(f"ALS prompt: {prompt}")
        logger.info(f"ALS attachments count: {len(attachments)}")

        data = await _call_als(prompt, attachments)
        duration = time.monotonic() - t0
        logger.debug(f"Raw ALS response: {data}")

        playbook_yaml, parsed = _extract_yaml(data.get("response", ""))
        parsed = sanitize_playbook(parsed)
        # TODO: validate playbook ops before AAP submission (block delete, shell, command modules)
        if parsed is not None:
            playbook_yaml = yaml.dump(parsed, default_flow_style=False, sort_keys=False)
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
                llm_summary=llm_summary,
                evidence_text=grounding_text,
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


async def _resolve_target(extra_vars: dict, llm_summary: dict | None) -> tuple[dict, str]:
    """Confirm the remediation target exists on the resolved spoke before launching.

    Fails closed: the LLM is free to name the affected component, but we mutate only a
    pod the cluster confirms. On success pod_name is retargeted to the live pod so the
    playbook derives the right deployment; on failure the caller blocks the launch.
    """
    if not extra_vars:
        return extra_vars, ""
    candidate = ""
    if llm_summary:
        candidate = _normalize_component_name(llm_summary.get("affected_component", ""))
    original = extra_vars.get("pod_name", "")
    candidate = candidate or original
    # No retargeting: don't gate the launch on a verify call that could fail transiently.
    if not candidate or candidate == original:
        return extra_vars, ""
    edge_site_id = extra_vars.get("edge_site_id", "")
    spec = await _invoke_tool(
        "get_pod_spec",
        {"name": candidate, "namespace": extra_vars.get("namespace", ""), "edge_site_id": edge_site_id},
    )
    if not spec.get("success"):
        site = edge_site_id or "default cluster"
        return extra_vars, f"target '{candidate}' not found on {site}: {spec.get('error', '')}"
    return {**extra_vars, "pod_name": spec.get("name") or candidate}, ""


async def _execute_in_aap(
    result: RemediationResult,
    name: str,
    yaml_content: str,
    log_event,
    llm_summary=None,
    evidence_text="",
) -> RemediationResult:
    logger.info(f"AAP execution pipeline started for playbook '{name}'")

    # Push playbook to Gitea
    commit = await _invoke_tool(
        "commit_playbook",
        {"playbook_name": name, "playbook_content": yaml_content},
    )
    if not commit.get("success"):
        return _aap_step_failed(result, commit, "commit_playbook")
    logger.info(f"Playbook committed to Gitea: file_path={commit.get('file_path', '')}")

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
    logger.info(f"Job template upserted: '{name}'")

    # Launch the job with target-specific variables
    extra_vars = build_launch_extra_vars(
        log_event, llm_summary=llm_summary, evidence_text=evidence_text
    )
    extra_vars, verify_error = await _resolve_target(extra_vars, llm_summary)
    if verify_error:
        return _aap_step_failed(result, {"error": verify_error}, "verify_target")
    launch = await _invoke_tool(
        "launch_job",
        {"job_template_name": name, "extra_vars": extra_vars},
    )
    if not launch.get("success"):
        return _aap_step_failed(result, launch, "launch_job")

    job_id = str(launch.get("job_id", ""))
    logger.info(f"AAP job launched: job_id={job_id} template='{name}'")
    return result.model_copy(
        update={
            "job_id": job_id,
            "output_summary": f"Launched AAP job {job_id} for {name} (pending)",
            "timestamp": now_iso(),
        },
    )
