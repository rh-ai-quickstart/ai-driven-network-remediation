"""AAP controller REST client — launches and inspects Ansible job templates.

decide_node picks the template by keyword match, so no model is in the loop and
there is nothing for an MCP tool layer to resolve. This calls AAP directly.
"""

from __future__ import annotations

import json

from loguru import logger

from ran_remediation_service.config import get_http_client


class AAPError(RuntimeError):
    """AAP rejected a request or returned no usable result."""


async def launch_job(template_name: str, extra_vars: dict | None = None) -> int:
    """Resolve a job template by name and launch it. Returns the AAP job id."""
    client = get_http_client()

    search = await client.get("/job_templates/", params={"name": template_name})
    search.raise_for_status()
    results = search.json().get("results", [])
    if not results:
        raise AAPError(f"Job template '{template_name}' not found")

    # AAP expects extra_vars as a JSON-encoded string, not a nested object.
    payload = {"extra_vars": json.dumps(extra_vars)} if extra_vars else {}
    launch = await client.post(f"/job_templates/{results[0]['id']}/launch/", json=payload)
    launch.raise_for_status()

    job_id = launch.json()["id"]
    logger.debug("AAP launch accepted job_id={} template={}", job_id, template_name)
    return job_id


async def get_job_status(job_id: int) -> dict:
    """Fetch a job's current state. Keys mirror the AAP controller job resource."""
    resp = await get_http_client().get(f"/jobs/{job_id}/")
    resp.raise_for_status()
    job = resp.json()
    return {
        "status": job.get("status"),
        "elapsed": job.get("elapsed", 0),
        "finished": job.get("finished"),
        "failed": job.get("failed", False),
        "result_traceback": job.get("result_traceback", ""),
    }


async def get_job_output(job_id: int, last_lines: int = 50) -> str:
    """Return the tail of a job's stdout."""
    resp = await get_http_client().get(f"/jobs/{job_id}/stdout/", params={"format": "txt"})
    resp.raise_for_status()
    lines = resp.text.splitlines()
    return "\n".join(lines[-last_lines:])
