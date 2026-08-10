"""AAP tool implementations."""

import base64
import json

import httpx

from .config import (
    AAP_API_PREFIX,
    AAP_CA_BUNDLE,
    AAP_TOKEN,
    AAP_URL,
    AAP_VERIFY_SSL,
    GITEA_OWNER,
    GITEA_REPO,
    GITEA_URL,
    get_gitea_token,
    mcp,
)


def _aap_client() -> httpx.Client:
    """Create an authenticated httpx client for the AAP REST API."""
    base = AAP_URL.rstrip("/")
    prefix = "/" + AAP_API_PREFIX.strip("/")
    verify = AAP_CA_BUNDLE if AAP_CA_BUNDLE else AAP_VERIFY_SSL
    return httpx.Client(
        base_url=f"{base}{prefix}",
        headers={"Authorization": f"Bearer {AAP_TOKEN}"},
        verify=verify,
        timeout=30,
    )


@mcp.tool()
def list_job_templates() -> dict:
    """
    List all available Ansible job templates in AAP.

    Returns:
        Dict with job_templates list: [{id, name, description, playbook}]
    """
    try:
        with _aap_client() as client:
            resp = client.get("/job_templates/?page_size=50")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"AAP API error: {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"AAP connection error: {e}"}

    templates = []
    for jt in data.get("results", []):
        templates.append(
            {
                "id": jt["id"],
                "name": jt["name"],
                "description": jt.get("description", ""),
                "playbook": jt.get("playbook", ""),
            }
        )

    return {"success": True, "job_templates": templates, "count": len(templates)}


def _resolve_credential_id(client: httpx.Client, name: str) -> int | None:
    """Look up an AAP credential by name and return its ID, or None."""
    resp = client.get("/credentials/", params={"name": name})
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None


@mcp.tool()
def launch_job(
    job_template_name: str,
    extra_vars: dict | None = None,
    credential_ids: list[int] | None = None,
    credential_name: str | None = None,
) -> dict:
    """
    Launch an Ansible job template by name.

    The automated flow attaches credentials to the template at deploy time,
    so credential_ids/credential_name are for ad-hoc use only.

    Args:
        job_template_name: Name of the job template to run (e.g., "restart-nginx")
        extra_vars:        Optional extra variables dict (e.g., {"namespace": "dark-noc-edge"})
        credential_ids:    Optional list of AAP credential IDs to attach (ad-hoc only)
        credential_name:   Optional credential name to resolve and attach (ad-hoc only)

    Returns:
        Dict with job_id and launch status
    """
    try:
        with _aap_client() as client:
            search_resp = client.get("/job_templates/", params={"name": job_template_name})
            search_resp.raise_for_status()
            results = search_resp.json().get("results", [])
            if not results:
                return {"success": False, "error": f"Job template '{job_template_name}' not found"}

            resolved = list(credential_ids) if credential_ids else []
            if credential_name:
                cred_id = _resolve_credential_id(client, credential_name)
                if cred_id is None:
                    return {"success": False, "error": f"Credential '{credential_name}' not found"}
                resolved.append(cred_id)

            payload = {}
            if extra_vars:
                payload["extra_vars"] = json.dumps(extra_vars)
            if resolved:
                payload["credentials"] = resolved

            template_id = results[0]["id"]
            launch_resp = client.post(f"/job_templates/{template_id}/launch/", json=payload)
            launch_resp.raise_for_status()
            job_data = launch_resp.json()
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"AAP API error: {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"AAP connection error: {e}"}

    return {
        "success": True,
        "job_id": job_data["id"],
        "job_url": f"{AAP_URL}/#/jobs/playbook/{job_data['id']}",
        "status": job_data.get("status", "pending"),
        "template_name": job_template_name,
    }


@mcp.tool()
def upsert_job_template(
    template_name: str,
    playbook: str,
    base_template_name: str = "lightspeed-runner",
    project_name: str | None = None,
) -> dict:
    """
    Ensure a job template exists for the given playbook path.
    If the template exists, patches the playbook field.
    If missing, copies from the base template then patches.

    Args:
        template_name:      Name for the job template
        playbook:           Playbook path within the AAP project
        base_template_name: Template to copy from if creating new (default: lightspeed-runner)

    Returns:
        Dict with template_id, created flag, and status
    """
    try:
        with _aap_client() as client:
            existing_resp = client.get(f"/job_templates/?name={template_name}")
            existing_resp.raise_for_status()
            existing = existing_resp.json().get("results", [])

            created = False
            if existing:
                template_id = int(existing[0]["id"])
            else:
                base_resp = client.get(f"/job_templates/?name={base_template_name}")
                base_resp.raise_for_status()
                base = base_resp.json().get("results", [])
                if not base:
                    return {"success": False, "error": f"Base template '{base_template_name}' not found"}
                base_id = int(base[0]["id"])
                copy_resp = client.post(f"/job_templates/{base_id}/copy/", json={"name": template_name})
                copy_resp.raise_for_status()
                copied = copy_resp.json()
                template_id = int(copied["id"])
                created = True

            patch_body = {
                "name": template_name,
                "playbook": playbook,
                "ask_variables_on_launch": True,
                "ask_credential_on_launch": True,
            }
            if project_name:
                proj_resp = client.get(
                    "/projects/",
                    params={"name": project_name},
                )
                proj_resp.raise_for_status()
                proj_results = proj_resp.json().get("results", [])
                if not proj_results:
                    return {
                        "success": False,
                        "error": f"Project '{project_name}' not found",
                    }
                patch_body["project"] = proj_results[0]["id"]

            patch_resp = client.patch(
                f"/job_templates/{template_id}/",
                json=patch_body,
            )
            if patch_resp.status_code >= 400:
                current_resp = client.get(f"/job_templates/{template_id}/")
                current_resp.raise_for_status()
                current = current_resp.json()
                if str(current.get("playbook", "")) == playbook:
                    return {
                        "success": True,
                        "created": created,
                        "template_id": int(current["id"]),
                        "template_name": current.get("name", template_name),
                        "playbook": str(current.get("playbook", "")),
                        "warning": f"idempotent-patch-{patch_resp.status_code}",
                    }
                return {
                    "success": False,
                    "created": created,
                    "template_id": template_id,
                    "error": f"patch failed: http-{patch_resp.status_code}",
                }

            jt = patch_resp.json()
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"AAP API error: {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"AAP connection error: {e}"}

    return {
        "success": True,
        "created": created,
        "template_id": int(jt["id"]),
        "template_name": jt["name"],
        "playbook": jt.get("playbook", playbook),
    }


@mcp.tool()
def commit_playbook(
    playbook_name: str,
    playbook_content: str,
) -> dict:
    """
    Commit a generated playbook file to the Gitea repository.

    Args:
        playbook_name:    Name for the playbook (without .yaml extension)
        playbook_content: The YAML content of the playbook

    Returns:
        Dict with success, file_path, and sha
    """
    filepath = f"playbooks/{playbook_name}.yaml"
    encoded = base64.b64encode(playbook_content.encode()).decode()
    url = f"{GITEA_URL}/api/v1/repos/{GITEA_OWNER}" f"/{GITEA_REPO}/contents/{filepath}"

    try:
        with httpx.Client(
            headers={"Authorization": f"token {get_gitea_token()}"},
            timeout=30,
        ) as client:
            existing_sha = None
            get_resp = client.get(url)
            if get_resp.status_code == 200:
                existing_sha = get_resp.json().get("sha")

            payload = {
                "content": encoded,
                "message": f"generated: {playbook_name}",
            }
            if existing_sha:
                payload["sha"] = existing_sha
                resp = client.put(url, json=payload)
            else:
                resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"Gitea API error: {e.response.status_code}",
        }
    except httpx.HTTPError as e:
        return {"success": False, "error": f"Gitea connection error: {e}"}

    return {
        "success": True,
        "file_path": filepath,
        "sha": data.get("content", {}).get("sha", ""),
    }


@mcp.tool()
def get_job_status(job_id: int) -> dict:
    """
    Get the current status of an Ansible job.

    Args:
        job_id: The job ID returned by launch_job

    Returns:
        Dict with status, elapsed time, and result summary
    """
    try:
        with _aap_client() as client:
            resp = client.get(f"/jobs/{job_id}/")
            resp.raise_for_status()
            job = resp.json()
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"AAP API error: {e.response.status_code}", "job_id": job_id}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"AAP connection error: {e}", "job_id": job_id}

    return {
        "success": True,
        "job_id": job_id,
        "status": job.get("status"),
        "elapsed": job.get("elapsed", 0),
        "started": job.get("started"),
        "finished": job.get("finished"),
        "failed": job.get("failed", False),
        "result_traceback": job.get("result_traceback", ""),
    }


@mcp.tool()
def get_job_output(job_id: int, last_lines: int = 50) -> dict:
    """
    Get stdout output from an Ansible job.

    Args:
        job_id:     Job ID to get output from
        last_lines: Number of output lines to return (default: 50)

    Returns:
        Dict with stdout text
    """
    try:
        with _aap_client() as client:
            resp = client.get(f"/jobs/{job_id}/stdout/?format=txt")
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"AAP API error: {e.response.status_code}", "job_id": job_id}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"AAP connection error: {e}", "job_id": job_id}

    lines = resp.text.splitlines()
    truncated = lines[-last_lines:] if len(lines) > last_lines else lines

    return {
        "success": True,
        "job_id": job_id,
        "output": "\n".join(truncated),
        "total_lines": len(lines),
        "truncated_to": last_lines,
    }


@mcp.tool()
def sync_project(
    project_name: str = "lightspeed-generated",
) -> dict:
    """
    Trigger an AAP Project Sync (fire-and-forget).

    Use get_project_update_status to poll for completion.

    Args:
        project_name: Name of the AAP project to sync (default: lightspeed-generated)

    Returns:
        Dict with success, project_id, and update_id
    """
    try:
        with _aap_client() as client:
            proj_resp = client.get("/projects/", params={"name": project_name})
            proj_resp.raise_for_status()
            projects = proj_resp.json().get("results", [])
            if not projects:
                return {"success": False, "error": f"Project '{project_name}' not found"}

            project_id = projects[0]["id"]

            update_resp = client.post(f"/projects/{project_id}/update/")
            update_resp.raise_for_status()
            update_id = update_resp.json()["project_update"]
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"AAP API error: {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"AAP connection error: {e}"}

    return {
        "success": True,
        "project_id": project_id,
        "update_id": update_id,
    }


@mcp.tool()
def get_project_update_status(update_id: int) -> dict:
    """
    Get the current status of an AAP project update.

    Args:
        update_id: The update ID returned by sync_project

    Returns:
        Dict with update_id, status, and finished timestamp
    """
    try:
        with _aap_client() as client:
            resp = client.get(f"/project_updates/{update_id}/")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"AAP API error: {e.response.status_code}", "update_id": update_id}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"AAP connection error: {e}", "update_id": update_id}

    return {
        "success": True,
        "update_id": update_id,
        "status": data.get("status"),
        "finished": data.get("finished"),
    }
