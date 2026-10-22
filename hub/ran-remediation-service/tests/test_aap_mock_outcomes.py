"""Classified incidents through the mock controller, audit, API, and card."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from ran_remediation_service.graph import build_graph

ROOT = Path(__file__).resolve().parents[3]
MOCK_PATH = ROOT / "hub/infra/aap-mock/main.py"
FIXTURE_PATH = ROOT / "hub/telco-oran/src/telco_oran/fixtures/antenna_failure.json"


def _mock_app():
    spec = importlib.util.spec_from_file_location("aap_mock_for_remediation", MOCK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "category", "template", "summary_term", "card_category"),
    [
        ("high_congestion_gradual", "congestion", "ran-load-balance", "load balancing", "Congestion"),
        ("high_congestion_sudden", "congestion", "ran-load-balance", "load balancing", "Congestion"),
        ("resource_allocation_bugs", "resource_allocation_fault", "ran-resource-allocation-correction", "allocation correction", "Resource Allocation Fault"),
        ("buffer_overflow", "buffer_overflow", "ran-buffer-relief", "buffer relief", "Buffer Overflow"),
        ("handover_fault", "handover_fault", "ran-handover-stabilization", "handover stabilization", "Handover Fault"),
        ("doppler_shift_severe", "doppler_shift", "ran-doppler-shift-response", "Doppler", "Doppler Shift"),
        ("rf_filter_fault", "rf_filter_fault", "ran-rf-filter-response", "RF filter", "RF Filter Fault"),
    ],
)
async def test_classified_incident_reaches_expected_mock_job_api_and_card(
    monkeypatch, scenario, category, template, summary_term, card_category,
):
    fixture = json.loads((ROOT / "hub/telco-oran/src/telco_oran/fixtures" / f"{scenario}.json").read_text())
    assert len(fixture["kpi_window"]) == 128
    assert len(fixture["kpi_window"][0]) == 18
    incident_id = f"{scenario}-001"
    enriched = {
        "incident_id": incident_id,
        "zone": fixture["zone"],
        "application": fixture["application"],
        "kpi_window": fixture["kpi_window"],
        "ad_label": "anomalous",
        "ad_confidence": 0.94,
        "ml_class": fixture["anomaly_class"],
        "ml_confidence": 0.9,
        "root_cause_category": category,
        "root_cause": "classified RAN fault",
        "recommended_fix": "Run the selected remediation",
    }
    mock = _mock_app()
    audited = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock.app), base_url="http://aap-mock/api/controller/v2"
    ) as aap:
        with (
            patch("ran_remediation_service.aap_client.get_http_client", return_value=aap),
            patch("ran_remediation_service.nodes.audit.publish_remediation_record", side_effect=lambda record: audited.append(record) or 0),
            patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
        ):
            result = await build_graph().ainvoke(enriched)

    assert result["template_name"] == template
    assert result["job_status"] == "successful"
    assert result["success"] is True
    assert summary_term.lower() in result["output_summary"].lower()
    assert "no RAN device changed" in result["output_summary"]
    assert "changed=0" in result["output_summary"]
    assert mock.jobs_db[int(result["job_id"])]["name"] == template
    assert all(job["name"] != "ran-generic-remediation" for job in mock.jobs_db.values())
    assert audited[0]["incident_id"] == incident_id
    assert audited[0]["template_name"] == template
    assert audited[0]["output_summary"] == result["output_summary"]

    monkeypatch.syspath_prepend(str(ROOT / "hub/telco-oran/src"))
    monkeypatch.syspath_prepend(str(ROOT / "hub/ran-chatbot-service/src"))
    from ran_chatbot_service import app as chatbot_app
    from ran_chatbot_service.models import EnrichedAnomaly

    connected_consumer = MagicMock(is_connected=True)
    with (
        patch("ran_chatbot_service.AnomaliesConsumer", return_value=connected_consumer),
        patch("ran_chatbot_service.RemediationConsumer", return_value=connected_consumer),
        patch("ran_chatbot_service._rca_ready", new_callable=AsyncMock, return_value=True),
        TestClient(chatbot_app) as chatbot,
    ):
        chatbot.app.state.recent_anomalies.append(EnrichedAnomaly.model_validate(enriched))
        chatbot.app.state.recent_remediations.append(audited[0])
        response = chatbot.get("/api/anomalies")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    incident = response.json()["anomalies"][0]
    assert incident["incident_id"] == incident_id
    assert incident["ml_class"] == fixture["anomaly_class"]
    assert incident["root_cause_category"] == category
    assert incident["remediation_template_name"] == template
    assert incident["remediation_job_id"] == result["job_id"]
    assert incident["remediation_job_status"] == "successful"
    assert incident["remediation_output_summary"] == result["output_summary"]

    if shutil.which("node") and (ROOT / "hub/ran-frontend/node_modules/vite").exists():
        card = subprocess.run(
            ["node", str(ROOT / "hub/ran-frontend/tests/render-incident-card.mjs")],
            cwd=ROOT / "hub/ran-frontend", input=json.dumps(incident),
            text=True, capture_output=True, check=True,
        ).stdout
        assert f"Incident {incident_id}" in card
        assert f"Mantis RCA: {fixture['anomaly_class']}" in card
        assert f"ML category: {card_category}" in card
        assert f"Job: {template}" in card
        assert "Job outcome: successful" in card
        assert "no RAN device changed" in card


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "stage", "job_status", "has_job"),
    [
        ("launch_error", "aap_launch", "failed", False),
        ("timeout", "aap_timeout", "timed_out", True),
        ("failed", "aap_job", "failed", True),
        ("successful", "", "successful", True),
    ],
)
async def test_classified_kpi_incident_reaches_expected_mock_outcome(outcome, stage, job_status, has_job, monkeypatch):
    mock = _mock_app()
    controller = TestClient(mock.app)
    templates = controller.get("/api/controller/v2/job_templates/", params={"name": "ran-cell-recovery"}).json()
    template_id = templates["results"][0]["id"]
    controller.patch(f"/api/controller/v2/job_templates/{template_id}/", json={"mock_outcome": outcome})
    fixture = json.loads(FIXTURE_PATH.read_text())
    classified = {
        "incident_id": "aap-failure-001", "zone": "A", "application": "Twitch",
        "kpi_window": fixture["kpi_window"], "ad_label": "anomalous", "ad_confidence": 0.94,
        "ml_class": "Antenna Failure", "ml_confidence": 0.91,
        "root_cause_category": "cell_failure", "root_cause": "cell outage", "recommended_fix": "recover cell",
    }
    assert len(classified["kpi_window"]) == 128
    audited = []
    transport = httpx.ASGITransport(app=mock.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://aap-mock/api/controller/v2") as aap:
        with (
            patch("ran_remediation_service.aap_client.get_http_client", return_value=aap),
            patch("ran_remediation_service.nodes.audit.publish_remediation_record", side_effect=lambda record: audited.append(record) or 0),
            patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
            patch("ran_remediation_service.nodes.remediate.JOB_TIMEOUT_SECONDS", 0.01),
            patch("ran_remediation_service.nodes.remediate.POLL_INTERVAL_SECONDS", 0.001),
        ):
            result = await build_graph().ainvoke(classified)

    assert result["template_name"] == "ran-cell-recovery"
    assert result["job_status"] == job_status
    assert bool(result["job_id"]) is has_job
    assert result["success"] is (outcome == "successful")
    assert result.get("failure_stage", "") == stage
    assert result["timestamp"]
    assert audited[0]["failure_stage"] == stage
    assert audited[0]["failure_reason"] == result.get("failure_reason", "")
    assert audited[0]["template_name"] == "ran-cell-recovery"
    assert all(job["name"] != "ran-generic-remediation" for job in mock.jobs_db.values())
    if outcome == "launch_error":
        assert result["failure_reason"] == "AAP request failed (HTTP 503)"
    elif outcome == "timeout":
        assert "timed out" in result["failure_reason"]
    elif outcome == "failed":
        assert result["failure_reason"] == "Simulated AAP job failure"
    else:
        assert result.get("failure_reason", "") == ""

    monkeypatch.syspath_prepend(str(ROOT / "hub/telco-oran/src"))
    monkeypatch.syspath_prepend(str(ROOT / "hub/ran-chatbot-service/src"))
    from ran_chatbot_service import app as chatbot_app
    from ran_chatbot_service.models import EnrichedAnomaly

    connected_consumer = MagicMock(is_connected=True)
    with (
        patch("ran_chatbot_service.AnomaliesConsumer", return_value=connected_consumer),
        patch("ran_chatbot_service.RemediationConsumer", return_value=connected_consumer),
        patch("ran_chatbot_service._rca_ready", new_callable=AsyncMock, return_value=True),
        TestClient(chatbot_app) as chatbot,
    ):
        chatbot.app.state.recent_anomalies.append(EnrichedAnomaly.model_validate(classified))
        chatbot.app.state.recent_remediations.append(audited[0])
        response = chatbot.get("/api/anomalies")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    incident = response.json()["anomalies"][0]
    assert incident["root_cause_category"] == "cell_failure"
    assert incident["ml_class"] == "Antenna Failure"
    assert incident["remediation_template_name"] == "ran-cell-recovery"
    assert incident["remediation_job_id"] == result["job_id"]
    assert incident["remediation_job_status"] == job_status
    assert incident["remediation_status"] == ("completed" if outcome == "successful" else "failed")
    assert incident["remediation_failure_stage"] == stage
    assert incident["remediation_failure_reason"] == audited[0]["failure_reason"]
    assert incident["remediation_timestamp"]

    frontend = ROOT / "hub/ran-frontend"
    if shutil.which("node") and (frontend / "node_modules/vite").exists():
        card = subprocess.run(
            ["node", str(frontend / "tests/render-incident-card.mjs")],
            cwd=frontend, input=json.dumps(incident), text=True, capture_output=True, check=True,
        ).stdout
        assert "ML category: Cell Failure" in card
        assert "Job: ran-cell-recovery" in card
        assert f"Job outcome: {job_status}" in card
        if has_job:
            assert f"Job ID: {result['job_id']}" in card
        else:
            assert "Job ID:" not in card
        if stage:
            assert f"{stage}: {audited[0]['failure_reason']}" in card
            assert "Remediation failed" in card
        else:
            assert "Remediated" in card


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "template_name"),
    [("cell_failure", "ran-cell-recovery"), ("rf_filter_fault", "ran-rf-filter-response"),
     ("doppler_shift", "ran-doppler-shift-response"),
     ("handover_fault", "ran-handover-stabilization"),
     ("buffer_overflow", "ran-buffer-relief")],
)
async def test_missing_selected_mock_template_does_not_launch_fallback(category, template_name):
    mock = _mock_app()
    mock.job_templates_db = {
        key: value for key, value in mock.job_templates_db.items() if value["name"] != template_name
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock.app), base_url="http://aap-mock/api/controller/v2"
    ) as aap:
        with (
            patch("ran_remediation_service.aap_client.get_http_client", return_value=aap),
            patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0),
            patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
        ):
            result = await build_graph().ainvoke({"incident_id": "missing-001", "root_cause_category": category})

    assert result["template_name"] == template_name
    assert result["failure_stage"] == "aap_launch"
    assert "not found" in result["failure_reason"]
    assert result["success"] is False
    assert mock.jobs_db == {}
