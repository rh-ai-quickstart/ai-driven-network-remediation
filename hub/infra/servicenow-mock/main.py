"""
ServiceNow Mock API
====================
Lightweight FastAPI service simulating the ServiceNow REST Table API.
Stores incidents in memory (lost on restart -- CI/demo only).

Uses the same REST contract as a real ServiceNow instance:
    POST   /api/now/table/incident            -> create incident (flat JSON)
    PATCH  /api/now/table/incident/{sys_id}   -> update incident (flat JSON)
    GET    /api/now/table/incident             -> list/query incidents (sysparm_query)
    GET    /api/now/table/sys_user             -> lookup user
    POST   /api/now/table/sys_user             -> create user

Authentication:
    HTTP Basic Auth (validated against SERVICENOW_USERNAME / SERVICENOW_PASSWORD env vars)

Note: Error responses use FastAPI's default shape ({"detail": "..."}), not
ServiceNow's ({"error": {"message": "...", "detail": "..."}, "status": "failure"}).
This is fine while the MCP server only checks status codes via raise_for_status();
align the error envelope here if client-side error-body parsing is ever added.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

app = FastAPI(title="ServiceNow Mock", version="2.0.0")
security = HTTPBasic()

MOCK_USERNAME = os.getenv("SERVICENOW_USERNAME", "admin")
MOCK_PASSWORD = os.getenv("SERVICENOW_PASSWORD", "admin")

incidents: dict[str, dict[str, Any]] = {}
_incidents_by_number: dict[str, str] = {}
users: dict[str, dict[str, Any]] = {}
_incident_counter = 1


def _verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not (
        secrets.compare_digest(credentials.username, MOCK_USERNAME)
        and secrets.compare_digest(credentials.password, MOCK_PASSWORD)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_number() -> str:
    global _incident_counter
    number = f"INC{_incident_counter:07d}"
    _incident_counter += 1
    return number


def _parse_sysparm_query(query: str) -> dict[str, str]:
    """Parse simple key=value pairs from a sysparm_query string."""
    params: dict[str, str] = {}
    if not query:
        return params
    for part in query.split("^"):
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.strip()] = value.strip()
    return params


def _apply_query_params(
    records: list[dict[str, Any]],
    sysparm_query: str,
    sysparm_limit: int,
    sysparm_fields: str,
) -> list[dict[str, Any]]:
    """Filter, limit, and project a list of records."""
    filters = _parse_sysparm_query(sysparm_query)
    for key, value in filters.items():
        records = [r for r in records if str(r.get(key, "")) == value]
    records = records[:sysparm_limit]
    if sysparm_fields:
        fields = [f.strip() for f in sysparm_fields.split(",")]
        records = [{k: r[k] for k in fields if k in r} for r in records]
    return records


# ─── Incident endpoints ──────────────────────────────────────────────────────


@app.post("/api/now/table/incident", status_code=201)
async def create_incident(body: dict[str, Any], _: str = Depends(_verify_basic_auth)):
    now = _now()
    sys_id = uuid.uuid4().hex
    number = _make_number()

    incident: dict[str, Any] = {
        "sys_id": sys_id,
        "number": number,
        "short_description": body.get("short_description", ""),
        "description": body.get("description", ""),
        "priority": body.get("priority", "3"),
        "state": body.get("state", "1"),
        "caller_id": body.get("caller_id", ""),
        "assignment_group": body.get("assignment_group", "NOC-Team"),
        "category": body.get("category", "Infrastructure"),
        "subcategory": body.get("subcategory", "OpenShift"),
        "urgency": body.get("urgency", "3"),
        "impact": body.get("impact", "3"),
        "sys_created_on": now,
        "sys_updated_on": now,
        "work_notes": "",
        "close_code": "",
        "close_notes": "",
        "resolved_by": "",
        "resolution_code": "",
    }
    incidents[sys_id] = incident
    _incidents_by_number[number] = sys_id
    return {"result": incident}


@app.patch("/api/now/table/incident/{sys_id}")
async def update_incident(sys_id: str, body: dict[str, Any], _: str = Depends(_verify_basic_auth)):
    if sys_id not in incidents:
        raise HTTPException(status_code=404, detail=f"Record not found: {sys_id}")

    inc = incidents[sys_id]
    inc.update(body)
    inc["sys_updated_on"] = _now()
    return {"result": inc}


@app.get("/api/now/table/incident")
async def list_incidents(
    sysparm_query: str = "",
    sysparm_limit: int = 100,
    sysparm_fields: str = "",
    _: str = Depends(_verify_basic_auth),
):
    filters = _parse_sysparm_query(sysparm_query)
    if list(filters.keys()) == ["number"] and filters["number"] in _incidents_by_number:
        sid = _incidents_by_number[filters["number"]]
        candidates = [incidents[sid]]
    else:
        candidates = list(incidents.values())
    return {"result": _apply_query_params(candidates, sysparm_query, sysparm_limit, sysparm_fields)}


# ─── User endpoints ──────────────────────────────────────────────────────────


@app.get("/api/now/table/sys_user")
async def get_user(
    sysparm_query: str = "",
    sysparm_limit: int = 10,
    sysparm_fields: str = "",
    _: str = Depends(_verify_basic_auth),
):
    return {
        "result": _apply_query_params(
            list(users.values()),
            sysparm_query,
            sysparm_limit,
            sysparm_fields,
        )
    }


@app.post("/api/now/table/sys_user", status_code=201)
async def create_user(body: dict[str, Any], _: str = Depends(_verify_basic_auth)):
    sys_id = uuid.uuid4().hex
    user = {"sys_id": sys_id, **body}
    users[sys_id] = user
    return {"result": user}


# ─── Health ───────────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "incidents_count": len(incidents)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
