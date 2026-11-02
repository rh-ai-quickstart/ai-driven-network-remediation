"""ServiceNow incident query."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import (
    SERVICENOW_PASSWORD,
    SERVICENOW_URL,
    SERVICENOW_USERNAME,
    SSL_VERIFY,
)

logger = logging.getLogger(__name__)


async def fetch_servicenow_incident_count() -> tuple[int, dict[str, Any]]:
    """Get open incident count from ServiceNow.

    Returns (count, servicenow_info_dict) with reachable only.
    """
    try:
        async with httpx.AsyncClient(timeout=8.0, verify=SSL_VERIFY) as client:
            resp = await client.get(
                f"{SERVICENOW_URL}/api/now/table/incident?sysparm_limit=100&sysparm_fields=number",
                auth=(SERVICENOW_USERNAME, SERVICENOW_PASSWORD),
            )
            if resp.status_code == 200:
                return len(resp.json().get("result", [])), {"reachable": True}
            logger.warning("ServiceNow returned HTTP %d", resp.status_code)
            return 0, {"reachable": False}
    except Exception:
        logger.warning("ServiceNow unreachable at %s", SERVICENOW_URL, exc_info=True)
        return 0, {"reachable": False}
