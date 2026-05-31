"""AAP tool implementations."""

import json

import httpx

from .config import (
    AAP_API_PREFIX,
    AAP_PASSWORD,
    AAP_URL,
    AAP_USERNAME,
    AAP_VERIFY_SSL,
    mcp,
)


def _aap_client() -> httpx.Client:
    """Create an authenticated httpx client for the AAP REST API."""
    base = AAP_URL.rstrip("/")
    prefix = "/" + AAP_API_PREFIX.strip("/")
    return httpx.Client(
        base_url=f"{base}{prefix}",
        auth=(AAP_USERNAME, AAP_PASSWORD),
        verify=AAP_VERIFY_SSL,
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


@mcp.tool()
def launch_job(
    job_template_name: str,
    extra_vars: dict | None = None,
) -> dict:
    """
    Launch an Ansible job template by name.

    Args:
        job_template_name: Name of the job template to run (e.g., "restart-nginx")
        extra_vars:        Optional extra variables dict (e.g., {"namespace": "dark-noc-edge"})

    Returns:
        Dict with job_id and launch status
    """
    try:
        with _aap_client() as client:
            search_resp = client.get(f"/job_templates/?name={job_template_name}")
            search_resp.raise_for_status()
            results = search_resp.json().get("results", [])

            if not results:
                return {"success": False, "error": f"Job template '{job_template_name}' not found"}

            template_id = results[0]["id"]

            payload = {}
            if extra_vars:
                payload["extra_vars"] = json.dumps(extra_vars)

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
    base_template_name: str = "lightspeed-generate-and-run",
) -> dict:
    """
    Ensure a job template exists for the given playbook path.
    If the template exists, patches the playbook field.
    If missing, copies from the base template then patches.

    Args:
        template_name:      Name for the job template
        playbook:           Playbook path within the AAP project
        base_template_name: Template to copy from if creating new (default: lightspeed-generate-and-run)

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

            patch_resp = client.patch(
                f"/job_templates/{template_id}/",
                json={"name": template_name, "playbook": playbook, "ask_variables_on_launch": True},
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
