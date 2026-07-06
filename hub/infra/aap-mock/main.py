"""
Slim AAP Mock -- covers the AAP v2 REST API surface used by mcp-aap tools.

"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="AAP Mock", version="0.1.0")

# ---------------------------------------------------------------------------
# In-memory databases
# ---------------------------------------------------------------------------
job_templates_db: dict[int, dict[str, Any]] = {}
jobs_db: dict[int, dict[str, Any]] = {}
job_events_db: dict[int, list[dict[str, Any]]] = {}

_next_template_id = 1
_next_job_id = 1
_next_event_id = 1


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Seed data -- two templates so list_job_templates returns real results
# ---------------------------------------------------------------------------
def _seed() -> None:
    global _next_template_id
    for name, playbook, desc in [
        ("restart-nginx", "restart.yml", "Restart nginx on edge"),
        ("scale-up-workers", "scale.yml", "Scale up worker replicas"),
        ("lightspeed-runner", "playbooks/lightspeed-generate-and-run.yaml", "Run OLS-generated playbook"),
    ]:
        job_templates_db[_next_template_id] = {
            "id": _next_template_id,
            "name": name,
            "description": desc,
            "job_type": "run",
            "inventory": 1,
            "project": 1,
            "playbook": playbook,
            "ask_variables_on_launch": True,
            "created": _now(),
            "modified": _now(),
            "url": f"/api/v2/job_templates/{_next_template_id}/",
        }
        _next_template_id += 1


_seed()

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Job templates
# ---------------------------------------------------------------------------


@app.get("/api/v2/job_templates/")
def list_templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    name: str | None = Query(None),
):
    templates = list(job_templates_db.values())
    if name is not None:
        templates = [t for t in templates if t["name"] == name]

    total = len(templates)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "count": total,
        "next": f"/api/v2/job_templates/?page={page + 1}&page_size={page_size}" if end < total else None,
        "previous": None,
        "results": templates[start:end],
    }


@app.get("/api/v2/job_templates/{template_id}/")
def get_template(template_id: int):
    if template_id not in job_templates_db:
        raise HTTPException(404, "Job template not found")
    return job_templates_db[template_id]


@app.post("/api/v2/job_templates/{template_id}/launch/")
def launch_template(template_id: int, body: dict[str, Any] | None = None):
    if template_id not in job_templates_db:
        raise HTTPException(404, "Job template not found")

    global _next_job_id, _next_event_id
    tmpl = job_templates_db[template_id]
    job_id = _next_job_id
    _next_job_id += 1

    now = _now()
    job = {
        "id": job_id,
        "name": tmpl["name"],
        "status": "successful",
        "started": now,
        "finished": now,
        "elapsed": 1.5,
        "failed": False,
        "job_template": template_id,
        "inventory": tmpl.get("inventory", 1),
        "project": tmpl.get("project", 1),
        "playbook": tmpl.get("playbook", "main.yml"),
        "extra_vars": body.get("extra_vars", "{}") if body else "{}",
        "result_traceback": "",
        "url": f"/api/v2/jobs/{job_id}/",
    }
    jobs_db[job_id] = job

    events = []
    for line in [
        f"PLAY [{tmpl['name']}] ***",
        "TASK [Gathering Facts] ***",
        "ok: [host1]",
        f"TASK [{tmpl['playbook']}] ***",
        "changed: [host1]",
        "PLAY RECAP ***",
        "host1 : ok=2 changed=1 unreachable=0 failed=0",
    ]:
        events.append(
            {
                "id": _next_event_id,
                "event": "runner_on_ok",
                "stdout": line,
                "event_display": line,
                "job": job_id,
            }
        )
        _next_event_id += 1
    job_events_db[job_id] = events

    logger.info("Launched job %d from template %d (%s)", job_id, template_id, tmpl["name"])
    return job


@app.post("/api/v2/job_templates/{template_id}/copy/")
def copy_template(template_id: int, body: dict[str, Any] | None = None):
    if template_id not in job_templates_db:
        raise HTTPException(404, "Job template not found")

    global _next_template_id
    src = job_templates_db[template_id]
    new_id = _next_template_id
    _next_template_id += 1

    copy = {**src, "id": new_id, "created": _now(), "modified": _now()}
    if body and "name" in body:
        copy["name"] = body["name"]
    copy["url"] = f"/api/v2/job_templates/{new_id}/"
    job_templates_db[new_id] = copy

    logger.info("Copied template %d -> %d", template_id, new_id)
    return copy


@app.patch("/api/v2/job_templates/{template_id}/")
def patch_template(template_id: int, body: dict[str, Any]):
    if template_id not in job_templates_db:
        raise HTTPException(404, "Job template not found")

    tmpl = job_templates_db[template_id]
    for key in ("name", "playbook", "description", "ask_variables_on_launch"):
        if key in body:
            tmpl[key] = body[key]
    tmpl["modified"] = _now()
    return tmpl


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@app.get("/api/v2/jobs/{job_id}/")
def get_job(job_id: int):
    if job_id not in jobs_db:
        raise HTTPException(404, "Job not found")
    return jobs_db[job_id]


@app.get("/api/v2/jobs/{job_id}/stdout/")
def get_job_stdout(job_id: int, format: str = Query("json")):
    if job_id not in job_events_db:
        raise HTTPException(404, "Job not found")

    lines = [e.get("stdout") or e.get("event_display", "") for e in job_events_db[job_id]]
    content = "\n".join(lines)

    if format in ("txt", "ansi"):
        return PlainTextResponse(content)

    return {
        "range": {"start": 0, "end": len(lines), "absolute_end": len(lines)},
        "content": content,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", "8080"))
    logger.info("Starting AAP Mock on port %d with %d seed templates", port, len(job_templates_db))
    uvicorn.run(app, host="0.0.0.0", port=port)
