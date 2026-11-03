"""ServiceNow tool implementations."""

import re
from urllib.parse import quote

import httpx

from .close_codes import load_close_code_fallbacks
from .config import (
    SNOW_API_KEY,
    SNOW_CALLER_NAME,
    SNOW_PASSWORD,
    SNOW_URL,
    SNOW_USERNAME,
    mcp,
)

MAX_SHORT_DESCRIPTION_LEN = 160


def _snow_client() -> httpx.Client:
    """Create httpx client for ServiceNow Table API.

    Uses ``x-sn-apikey`` when ``SERVICENOW_API_KEY`` is set (production
    noc_agent path). Falls back to Basic Auth for mock and admin bootstrap.
    """
    headers = {"Content-Type": "application/json"}
    client_kwargs: dict = {
        "base_url": f"{SNOW_URL}/api/now",
        "headers": headers,
        "timeout": 15,
    }
    if SNOW_API_KEY:
        headers["x-sn-apikey"] = SNOW_API_KEY
    else:
        client_kwargs["auth"] = (SNOW_USERNAME, SNOW_PASSWORD)
    return httpx.Client(**client_kwargs)


def _lookup_incident(client: httpx.Client, ticket_number: str) -> dict:
    """Find incident by ticket number via sysparm_query and return the record."""
    query = quote(f"number={ticket_number}", safe="")
    resp = client.get(f"/table/incident?sysparm_query={query}&sysparm_limit=1")
    resp.raise_for_status()
    results = resp.json().get("result", [])
    if not results:
        raise ValueError(f"Incident not found: {ticket_number}")
    return results[0]


def _instance_close_codes(client: httpx.Client) -> set[str]:
    """Return close_code labels/values configured on this ServiceNow instance."""
    resp = client.get(
        "/table/sys_choice",
        params={
            "sysparm_query": "element=close_code^name=incident^inactive=false",
            "sysparm_fields": "label,value",
            "sysparm_limit": 100,
        },
    )
    resp.raise_for_status()
    codes: set[str] = set()
    for choice in resp.json().get("result", []):
        label = str(choice.get("label", "")).strip()
        value = str(choice.get("value", "")).strip()
        if label:
            codes.add(label)
        if value:
            codes.add(value)
    return codes


def _resolve_close_codes(client: httpx.Client, resolution_code: str | None) -> list[str]:
    """Pick close_code candidates: explicit value, instance choice, then shared fallbacks."""
    if resolution_code:
        return [resolution_code]

    fallbacks = load_close_code_fallbacks()
    try:
        instance_codes = _instance_close_codes(client)
    except httpx.HTTPError:
        return fallbacks

    if not instance_codes:
        return fallbacks

    matched = [code for code in fallbacks if code in instance_codes]
    if matched:
        return matched

    first_instance_code = next(iter(instance_codes))
    return [first_instance_code, *fallbacks]


def _resolve_or_create_caller_sys_id(client: httpx.Client, display_name: str) -> str:
    """Return sys_id for caller display name; create user if missing."""
    query = quote(f"name={display_name}", safe="")
    resp = client.get(f"/table/sys_user?sysparm_query={query}&sysparm_limit=1&sysparm_fields=sys_id,name,user_name")
    resp.raise_for_status()
    results = resp.json().get("result", [])
    if results:
        return results[0].get("sys_id", "")

    user_name = re.sub(r"[^a-z0-9]+", ".", display_name.lower()).strip(".")
    create_resp = client.post(
        "/table/sys_user",
        json={
            "name": display_name,
            "user_name": user_name or "noc.agent",
            "active": "true",
        },
    )
    create_resp.raise_for_status()
    return create_resp.json().get("result", {}).get("sys_id", "")


def _incident_url(sys_id: str, ticket_number: str = "") -> str:
    """Build a ServiceNow incident URL."""
    if sys_id:
        return f"{SNOW_URL}/incident.do?sys_id={sys_id}"
    if ticket_number:
        return f"{SNOW_URL}/incident_list.do?sysparm_query={quote(f'number={ticket_number}', safe='')}"
    return f"{SNOW_URL}/incident_list.do"


@mcp.tool()
def create_incident(
    short_description: str,
    description: str,
    priority: int = 3,
    assignment_group: str = "NOC-Team",
    category: str = "Infrastructure",
    subcategory: str = "OpenShift",
) -> dict:
    """
    Create a new ServiceNow incident ticket.

    Args:
        short_description: Brief one-line description (max 160 chars)
        description:       Full incident details with symptoms and context
        priority:          1=Critical, 2=High, 3=Medium, 4=Low
        assignment_group:  Team to assign to (default: NOC-Team)
        category:          Incident category (default: Infrastructure)
        subcategory:       Incident subcategory (default: OpenShift)

    Returns:
        Dict with ticket_number, sys_id, and incident URL
    """
    try:
        with _snow_client() as client:
            caller_value = SNOW_CALLER_NAME
            caller_sys_id = _resolve_or_create_caller_sys_id(client, SNOW_CALLER_NAME)
            if caller_sys_id:
                caller_value = caller_sys_id

            payload = {
                "short_description": short_description[:MAX_SHORT_DESCRIPTION_LEN],
                "description": description,
                "priority": str(priority),
                "caller_id": caller_value,
                "assignment_group": assignment_group,
                "category": category,
                "subcategory": subcategory,
                "state": "1",
                "urgency": str(priority),
                "impact": str(priority),
            }
            resp = client.post("/table/incident", json=payload)
            resp.raise_for_status()
            data = resp.json().get("result", {})

        return {
            "success": True,
            "ticket_number": data.get("number", ""),
            "sys_id": data.get("sys_id", ""),
            "state": "New",
            "priority": priority,
            "short_description": short_description[:MAX_SHORT_DESCRIPTION_LEN],
            "caller_name": SNOW_CALLER_NAME,
            "incident_url": _incident_url(data.get("sys_id", ""), data.get("number", "")),
        }
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"ServiceNow API error: {e.response.status_code} – {e.response.text[:200]}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"ServiceNow connection error: {e}"}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def update_incident(
    ticket_number: str,
    work_notes: str,
    state: str = "",
) -> dict:
    """
    Add work notes or update the state of an existing incident.

    Args:
        ticket_number: ServiceNow incident number (e.g., INC0001234)
        work_notes:    Text to add as work notes
        state:         New state: "in_progress", "resolved", or "" (no change)

    Returns:
        Dict with update confirmation
    """
    try:
        state_map = {"in_progress": "2", "resolved": "6", "closed": "7", "": ""}

        payload: dict[str, str] = {"work_notes": work_notes}
        if state and state in state_map:
            payload["state"] = state_map[state]

        with _snow_client() as client:
            record = _lookup_incident(client, ticket_number)
            sys_id = record["sys_id"]
            resp = client.patch(f"/table/incident/{sys_id}", json=payload)
            resp.raise_for_status()

        return {
            "success": True,
            "ticket_number": ticket_number,
            "updated_state": state or "unchanged",
        }
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"ServiceNow API error: {e.response.status_code} – {e.response.text[:200]}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"ServiceNow connection error: {e}"}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_incident(ticket_number: str) -> dict:
    """
    Get details of an existing ServiceNow incident.

    Args:
        ticket_number: ServiceNow incident number (e.g., INC0001234)

    Returns:
        Dict with full incident details
    """
    try:
        with _snow_client() as client:
            data = _lookup_incident(client, ticket_number)

        state_labels = {"1": "New", "2": "In Progress", "3": "On Hold", "6": "Resolved", "7": "Closed"}

        return {
            "ticket_number": ticket_number,
            "short_description": data.get("short_description", ""),
            "state": state_labels.get(str(data.get("state", "1")), "Unknown"),
            "priority": data.get("priority", ""),
            "assignment_group": data.get("assignment_group", ""),
            "created": data.get("sys_created_on", ""),
            "updated": data.get("sys_updated_on", ""),
        }
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"ServiceNow API error: {e.response.status_code} – {e.response.text[:200]}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"ServiceNow connection error: {e}"}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def resolve_incident(
    ticket_number: str,
    resolution_notes: str,
    resolution_code: str | None = None,
) -> dict:
    """
    Resolve a ServiceNow incident with resolution notes.

    Args:
        ticket_number:    ServiceNow incident number
        resolution_notes: Explanation of how the issue was resolved
        resolution_code:  Standard resolution code; when omitted, tries PDI-specific fallbacks

    Returns:
        Dict with resolution confirmation
    """
    last_error = ""

    try:
        with _snow_client() as client:
            record = _lookup_incident(client, ticket_number)
            sys_id = record["sys_id"]
            codes_to_try = _resolve_close_codes(client, resolution_code)

            for close_code in codes_to_try:
                payload = {
                    "state": "6",
                    "close_code": close_code,
                    "resolution_code": close_code,
                    "close_notes": resolution_notes,
                    "resolved_by": "noc-agent",
                }
                resp = client.patch(f"/table/incident/{sys_id}", json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    last_error = f"ServiceNow API error: {e.response.status_code} – {e.response.text[:200]}"
                    continue

                return {
                    "success": True,
                    "ticket_number": ticket_number,
                    "state": "Resolved",
                    "resolution_code": close_code,
                }

        return {"success": False, "error": last_error or "Failed to resolve incident"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"ServiceNow API error: {e.response.status_code} – {e.response.text[:200]}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"ServiceNow connection error: {e}"}
    except ValueError as e:
        return {"success": False, "error": str(e)}
