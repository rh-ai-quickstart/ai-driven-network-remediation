"""End-to-end Telco O-RAN incident outcome test.

Drives the whole Workflow 2 chain from the chatbot BFF: a demo trigger publishes
a TelecomTS fixture to Kafka, ran-anomaly-detector flags it, ran-rca-service
enriches it, and ran-remediation-service launches an AAP job template. The
chatbot joins the remediation result or RCA failure status by incident_id.
The Kind PR environment has no RCA classifier and must stop at classification;
deployments with a classifier must reach the AAP remediation job.

Set RAN_CHATBOT_SERVICE_URL to override the default http://localhost:8008 and
REMEDIATION_E2E_TIMEOUT to allow more than 240s for the chain to settle.
RCA_E2E_EXPECT_CLASSIFIER=false selects the Kind failure-status expectation.
"""

import os
import time

import pytest

pytestmark = pytest.mark.telco

_TIMEOUT_SECONDS = int(os.environ.get("REMEDIATION_E2E_TIMEOUT", "240"))
_POLL_SECONDS = 5
_EXPECT_CLASSIFIER = os.environ.get("RCA_E2E_EXPECT_CLASSIFIER", "true").lower() == "true"


def _find(anomalies: list[dict], incident_id: str) -> dict | None:
    return next((a for a in anomalies if a.get("incident_id") == incident_id), None)


def test_demo_trigger_reaches_expected_incident_outcome(ran_chatbot_client):
    trigger = ran_chatbot_client.post("/api/demo/trigger", json={"scenario": "antenna_failure"})
    assert trigger.status_code == 200
    incident_id = trigger.json()["incident_id"]

    deadline = time.monotonic() + _TIMEOUT_SECONDS
    anomaly = None
    while time.monotonic() < deadline:
        response = ran_chatbot_client.get("/api/anomalies")
        assert response.status_code == 200
        anomaly = _find(response.json()["anomalies"], incident_id)
        if anomaly is not None and (
            anomaly["remediation_status"] is not None or anomaly["incident_status"] is not None
        ):
            break
        time.sleep(_POLL_SECONDS)
    else:
        stage = "was never detected" if anomaly is None else "had no final outcome"
        pytest.fail(f"incident {incident_id} {stage} within {_TIMEOUT_SECONDS}s (last seen: {anomaly})")

    if not _EXPECT_CLASSIFIER:
        status = anomaly["incident_status"]
        assert status is not None, anomaly
        assert status["incident_id"] == incident_id
        assert status["stage"] == "rca_classification"
        assert status["outcome"] == "failed"
        assert status["error_reason"] == "classifier not configured"
        assert anomaly["remediation_status"] is None, anomaly
        assert anomaly["remediation_job_id"] is None, anomaly
        return

    assert anomaly["incident_status"] is None, anomaly
    assert anomaly["remediation_status"] == "completed", anomaly
    assert anomaly["remediation_job_id"]
