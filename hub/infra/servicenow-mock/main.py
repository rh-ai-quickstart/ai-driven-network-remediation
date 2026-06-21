"""
ServiceNow Mock API
====================
Lightweight FastAPI service simulating the ServiceNow REST Table API.
Stores incidents in memory (lost on restart -- CI/demo only).

Endpoints:
    POST   /api/now/table/incident           -> create incident
    PATCH  /api/now/table/incident/{number}  -> update incident
    GET    /api/now/table/incident/{number}  -> get incident
    GET    /api/now/table/incident           -> list incidents
    GET    /api/now/table/sys_user           -> lookup user
    POST   /api/now/table/sys_user           -> create user

Authentication:
    Header: X-API-Key (validated against API_KEY env var)
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ServiceNow Mock", version="1.0.0")

API_KEY = os.getenv("API_KEY", "demo-api-key-2026")

incidents: dict[str, dict[str, Any]] = {}
users: dict[str, dict[str, Any]] = {}
_incident_counter = 1


def _verify_api_key(x_api_key: str = Header(default="")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_number() -> str:
    global _incident_counter
    number = f"INC{_incident_counter:07d}"
    _incident_counter += 1
    return number


class IncidentRecord(BaseModel):
    short_description: str = ""
    description: str = ""
    priority: str = "3"
    state: str = "1"
    caller_id: str = ""
    assignment_group: str = "NOC-Team"
    category: str = "Infrastructure"
    subcategory: str = "OpenShift"
    urgency: str = "3"
    impact: str = "3"
    work_notes: str = ""
    close_code: str = ""
    close_notes: str = ""
    resolved_by: str = ""


class IncidentCreateBody(BaseModel):
    record: IncidentRecord


class IncidentUpdateBody(BaseModel):
    record: dict[str, Any]


# ─── Incident endpoints ──────────────────────────────────────────────────────


@app.post("/api/now/table/incident", status_code=201)
async def create_incident(body: IncidentCreateBody, _: str = Depends(_verify_api_key)):
    now = _now()
    sys_id = uuid.uuid4().hex
    number = _make_number()

    incident: dict[str, Any] = {
        "sys_id": sys_id,
        "number": number,
        "short_description": body.record.short_description,
        "description": body.record.description,
        "priority": body.record.priority,
        "state": body.record.state,
        "caller_id": body.record.caller_id,
        "assignment_group": body.record.assignment_group,
        "category": body.record.category,
        "subcategory": body.record.subcategory,
        "urgency": body.record.urgency,
        "impact": body.record.impact,
        "sys_created_on": now,
        "sys_updated_on": now,
        "work_notes": [],
        "close_code": "",
        "close_notes": "",
        "resolved_by": "",
    }
    incidents[number] = incident
    return _wrap(incident)


@app.patch("/api/now/table/incident/{number}")
async def update_incident(number: str, body: IncidentUpdateBody, _: str = Depends(_verify_api_key)):
    if number not in incidents:
        raise HTTPException(status_code=404, detail=f"Incident {number} not found")

    inc = incidents[number]
    updates = body.record.copy()

    if "work_notes" in updates:
        wn = updates.pop("work_notes")
        if isinstance(inc["work_notes"], list):
            inc["work_notes"].append({"timestamp": _now(), "text": wn})
        else:
            inc["work_notes"] = [{"timestamp": _now(), "text": wn}]

    inc.update(updates)
    inc["sys_updated_on"] = _now()
    return _wrap(inc)


@app.get("/api/now/table/incident/{number}")
async def get_incident(number: str, _: str = Depends(_verify_api_key)):
    if number not in incidents:
        raise HTTPException(status_code=404, detail=f"Incident {number} not found")
    return _wrap(incidents[number])


@app.get("/api/now/table/incident")
async def list_incidents(
    state: Optional[str] = None,
    priority: Optional[str] = None,
    _: str = Depends(_verify_api_key),
):
    results = list(incidents.values())
    if state:
        results = [i for i in results if i["state"] == state]
    if priority:
        results = [i for i in results if i["priority"] == priority]
    return {"result": results, "count": len(results)}


# ─── User endpoints (for caller_id resolution in real mode) ──────────────────


@app.get("/api/now/table/sys_user")
async def get_user(
    sysparm_query: str = "",
    sysparm_limit: int = 10,
    sysparm_fields: str = "",
    _: str = Depends(_verify_api_key),
):
    if "name=" in sysparm_query:
        name = sysparm_query.split("name=", 1)[1]
        matches = [u for u in users.values() if u["name"] == name]
        return {"result": matches[:sysparm_limit]}
    return {"result": list(users.values())[:sysparm_limit]}


@app.post("/api/now/table/sys_user", status_code=201)
async def create_user(body: dict[str, Any], _: str = Depends(_verify_api_key)):
    sys_id = uuid.uuid4().hex
    user = {"sys_id": sys_id, **body}
    users[sys_id] = user
    return {"result": user}


# ─── Health ───────────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "incidents_count": len(incidents)}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _wrap(incident: dict) -> dict:
    """Wrap response in the format mcp-servicenow mock mode expects."""
    return {"record": incident}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
