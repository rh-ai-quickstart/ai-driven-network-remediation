"""ServiceNow tool implementations."""

import re
from urllib.parse import quote

import httpx

from .config import (
    SLACK_BASE_URL,
    SLACK_BOT_TOKEN,
    SLACK_NOC_CHANNEL,
    SNOW_CALLER_NAME,
    SNOW_PASSWORD,
    SNOW_URL,
    SNOW_USERNAME,
    mcp,
)

MAX_SHORT_DESCRIPTION_LEN = 160


def _snow_client() -> httpx.Client:
    """Create httpx client for ServiceNow API — always uses Basic Auth."""
    return httpx.Client(
        base_url=f"{SNOW_URL}/api/now",
        headers={"Content-Type": "application/json"},
        auth=(SNOW_USERNAME, SNOW_PASSWORD),
        timeout=15,
    )


def _lookup_incident(client: httpx.Client, ticket_number: str) -> dict:
    """Find incident by ticket number via sysparm_query and return the record."""
    query = quote(f"number={ticket_number}", safe="")
    resp = client.get(f"/table/incident?sysparm_query={query}&sysparm_limit=1")
    resp.raise_for_status()
    results = resp.json().get("result", [])
    if not results:
        raise ValueError(f"Incident not found: {ticket_number}")
    return results[0]


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


def _notify_slack_ticket_created(ticket: dict) -> dict:
    """Send a Slack notification when a ServiceNow incident is created."""
    if not SLACK_BOT_TOKEN:
        return {"sent": False, "reason": "missing_token"}

    text = (
        f":ticket: ServiceNow incident created\n"
        f"- Number: {ticket.get('ticket_number', '')}\n"
        f"- Priority: {ticket.get('priority', '')}\n"
        f"- Caller: {SNOW_CALLER_NAME}\n"
        f"- State: {ticket.get('state', 'New')}\n"
        f"- Short Description: {ticket.get('short_description', '')}\n"
        f"- URL: {ticket.get('incident_url', '')}"
    )
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        with httpx.Client(timeout=12) as http:
            resp = http.post(
                f"{SLACK_BASE_URL}/chat.postMessage", json={"channel": SLACK_NOC_CHANNEL, "text": text}, headers=headers
            )
            data = resp.json()
        if not data.get("ok", False):
            return {"sent": False, "reason": data.get("error", "unknown_error")}
        return {"sent": True, "ts": data.get("ts", "")}
    except Exception as exc:
        return {"sent": False, "reason": str(exc)}


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

        result = {
            "success": True,
            "ticket_number": data.get("number", ""),
            "sys_id": data.get("sys_id", ""),
            "state": "New",
            "priority": priority,
            "short_description": short_description[:MAX_SHORT_DESCRIPTION_LEN],
            "caller_name": SNOW_CALLER_NAME,
            "incident_url": _incident_url(data.get("sys_id", ""), data.get("number", "")),
        }
        result["slack_notification"] = _notify_slack_ticket_created(result)
        return result
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
    resolution_code: str = "Solved (Permanently)",
) -> dict:
    """
    Resolve a ServiceNow incident with resolution notes.

    Args:
        ticket_number:    ServiceNow incident number
        resolution_notes: Explanation of how the issue was resolved
        resolution_code:  Standard resolution code

    Returns:
        Dict with resolution confirmation
    """
    try:
        payload = {
            "state": "6",
            "close_code": resolution_code,
            "resolution_code": resolution_code,
            "close_notes": resolution_notes,
            "resolved_by": "noc-agent",
        }

        with _snow_client() as client:
            record = _lookup_incident(client, ticket_number)
            sys_id = record["sys_id"]
            resp = client.patch(f"/table/incident/{sys_id}", json=payload)
            resp.raise_for_status()

        return {
            "success": True,
            "ticket_number": ticket_number,
            "state": "Resolved",
            "resolution_code": resolution_code,
        }
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"ServiceNow API error: {e.response.status_code} – {e.response.text[:200]}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"ServiceNow connection error: {e}"}
    except ValueError as e:
        return {"success": False, "error": str(e)}
