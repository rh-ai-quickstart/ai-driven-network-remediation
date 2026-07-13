"""Kafka agent loop E2E: demo trigger → agent workflow → incident-audit.

Requires a deployed hub stack (chatbot BFF, Kafka, agent-service) with port-forwards
as set up by ``make integration-tests``.
"""

from __future__ import annotations

import os
import time

import pytest

_AUDIT_POLL_TIMEOUT_S = int(os.environ.get("KAFKA_E2E_TIMEOUT_SECONDS", "180"))
_AUDIT_POLL_INTERVAL_S = int(os.environ.get("KAFKA_E2E_POLL_INTERVAL_SECONDS", "5"))

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

    while time.monotonic() < deadline:
        response = chatbot_client.get("/api/integrations", params={"force_refresh": True})
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

        time.sleep(_AUDIT_POLL_INTERVAL_S)

    pytest.fail(
        f"incident_id {incident_id} not found in incident-audit within "
        f"{_AUDIT_POLL_TIMEOUT_S}s. Last incident_movie ({len(last_movie)} entries): "
        f"{last_movie}"
    )


@pytest.mark.integration
@pytest.mark.flaky(reruns=3)
def test_kafka_agent_loop(chatbot_client):
    """Demo trigger publishes to system-alerts; agent consumes and writes incident-audit."""
    trigger_resp = chatbot_client.post(
        "/api/demo/trigger",
        json={"scenario": "oom", "site": "edge-01"},
    )
    if trigger_resp.status_code == 502:
        pytest.fail(f"Demo trigger failed (Kafka unreachable): {trigger_resp.text}")
    assert trigger_resp.status_code == 200, trigger_resp.text
    trigger = trigger_resp.json()
    assert trigger["status"] == "queued"
    assert trigger["scenario"] == "oom"
    assert trigger["site"] == "edge-01"
    assert "kafka_offset" in trigger
    assert trigger["kafka_offset"] is not None
    incident_id = trigger["incident_id"]
    assert incident_id

    movie_entry = _poll_incident_movie(chatbot_client, incident_id)

    assert movie_entry["incident_id"] == incident_id
    assert movie_entry["stage"] in _COMPLETED_WORKFLOW_STAGES, (
        f"Workflow did not complete; stage={movie_entry.get('stage')!r}"
    )
    assert "edge-01" in movie_entry.get("title", "")
    assert movie_entry.get("summary")
