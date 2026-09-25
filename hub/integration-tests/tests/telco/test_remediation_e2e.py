"""End-to-end Telco O-RAN remediation test.

Drives the whole Workflow 2 chain from the chatbot BFF: a demo trigger publishes
a TelecomTS fixture to Kafka, ran-anomaly-detector flags it, ran-rca-service
enriches it, and ran-remediation-service launches an AAP job template. The
chatbot joins the remediation result back onto the anomaly by incident_id, so
polling /api/anomalies until remediation_status flips is the cheapest way to
assert the full chain — including the direct AAP controller call — worked.

Set RAN_CHATBOT_SERVICE_URL to override the default http://localhost:8008 and
REMEDIATION_E2E_TIMEOUT to allow more than 240s for the chain to settle.
"""

import os
import time

import pytest

pytestmark = pytest.mark.telco

_TIMEOUT_SECONDS = int(os.environ.get("REMEDIATION_E2E_TIMEOUT", "240"))
_POLL_SECONDS = 5


def _find(anomalies: list[dict], incident_id: str) -> dict | None:
    return next((a for a in anomalies if a.get("incident_id") == incident_id), None)


def test_demo_trigger_reaches_completed_remediation(ran_chatbot_client):
    trigger = ran_chatbot_client.post("/api/demo/trigger", json={"scenario": "antenna_failure"})
    assert trigger.status_code == 200
    incident_id = trigger.json()["incident_id"]

    deadline = time.monotonic() + _TIMEOUT_SECONDS
    anomaly = None
    while time.monotonic() < deadline:
        response = ran_chatbot_client.get("/api/anomalies")
        assert response.status_code == 200
        anomaly = _find(response.json()["anomalies"], incident_id)
        if anomaly is not None and anomaly["remediation_status"] is not None:
            break
        time.sleep(_POLL_SECONDS)
    else:
        stage = "was never detected" if anomaly is None else "was detected but never remediated"
        pytest.fail(
            f"incident {incident_id} {stage} within {_TIMEOUT_SECONDS}s (last seen: {anomaly})"
        )

    assert anomaly["remediation_status"] == "completed", anomaly
    assert anomaly["remediation_job_id"]
