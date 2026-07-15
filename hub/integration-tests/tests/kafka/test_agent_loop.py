"""Kafka agent loop E2E: demo trigger → agent workflow → incident-audit.

Requires a deployed hub stack (chatbot BFF, Kafka, agent-service) with port-forwards
as set up by ``make integration-tests``.

Kafka tests run before other integration tests (see ``pytest_collection_modifyitems`` in
``conftest.py``) because ``chatbot_service/test_bff.py`` also publishes a demo event via
``test_demo_trigger``. The agent consumer handles one alert at a time and can block for
up to GRAPH_INVOKE_TIMEOUT_SECONDS (300s).
"""

from __future__ import annotations

import os
import time

import pytest

from common_helpers import wait_for_agent_ready

# Demo scenarios without _overrides invoke Granite LLM analysis and can exceed the
# agent graph timeout in CI before audit_node runs. lightspeed embeds confidence overrides.
_DEMO_SCENARIO = "lightspeed"
_DEMO_SITE = "edge-01"

# Buffer for Kafka consume lag; lightspeed path completes in seconds when overrides apply.
_AUDIT_POLL_TIMEOUT_S = int(os.environ.get("KAFKA_E2E_TIMEOUT_SECONDS", "120"))

_COMPLETED_WORKFLOW_STAGES = frozenset({"Auto-Remediated", "Remediated", "Escalated"})


def _kafka_reachable(deps: dict) -> bool:
    if deps.get("status") == "ok":
        return True
    unavailable = deps.get("unavailable") or []
    return "kafka" not in unavailable


def _poll_incident_movie(chatbot_client, incident_id: str) -> dict:
    """Poll BFF integrations until incident_id appears in incident-audit timeline."""
    deadline = time.monotonic() + _AUDIT_POLL_TIMEOUT_S
    last_movie: list[dict] = []
    backoff = 1

    while time.monotonic() < deadline:
        response = chatbot_client.get(
            "/api/integrations",
            params={"force_refresh": True},
            timeout=60.0,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert _kafka_reachable(data.get("_deps", {})), (
            f"Kafka unreachable from chatbot BFF: {data.get('_deps')}"
        )

        movie = data.get("incident_movie", [])
        last_movie = movie
        for entry in movie:
            if entry.get("incident_id") == incident_id:
                return entry

        time.sleep(backoff)
        backoff = min(backoff * 2, 8)

    pytest.fail(
        f"incident_id {incident_id} not found in incident-audit within "
        f"{_AUDIT_POLL_TIMEOUT_S}s. Last incident_movie ({len(last_movie)} entries): "
        f"{last_movie}"
    )


@pytest.mark.integration
@pytest.mark.flaky(reruns=1)
def test_kafka_agent_loop(chatbot_client):
    """Demo trigger publishes to system-alerts; agent consumes and writes incident-audit."""
    wait_for_agent_ready()

    trigger_resp = chatbot_client.post(
        "/api/demo/trigger",
        json={"scenario": _DEMO_SCENARIO, "site": _DEMO_SITE},
    )
    if trigger_resp.status_code == 502:
        pytest.fail(f"Demo trigger failed (Kafka unreachable): {trigger_resp.text}")
    assert trigger_resp.status_code == 200, trigger_resp.text
    trigger = trigger_resp.json()
    assert trigger["status"] == "queued"
    assert trigger["scenario"] == _DEMO_SCENARIO
    assert trigger["site"] == _DEMO_SITE
    assert "kafka_offset" in trigger
    assert trigger["kafka_offset"] is not None
    incident_id = trigger["incident_id"]
    assert incident_id

    movie_entry = _poll_incident_movie(chatbot_client, incident_id)

    assert movie_entry["incident_id"] == incident_id
    assert movie_entry["stage"] in _COMPLETED_WORKFLOW_STAGES, (
        f"Workflow did not complete; stage={movie_entry.get('stage')!r}"
    )
    assert _DEMO_SITE in movie_entry.get("title", "")
    assert movie_entry.get("summary")
