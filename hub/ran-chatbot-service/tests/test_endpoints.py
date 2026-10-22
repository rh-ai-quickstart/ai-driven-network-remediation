"""Unit tests for the RAN chatbot BFF endpoints."""

import json
from unittest.mock import AsyncMock, patch

import pytest


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "ran-chatbot-bff"
    assert "version" in data


@patch("ran_chatbot_service.probe_http", new_callable=AsyncMock)
def test_ready_all_up(mock_probe, client):
    mock_probe.return_value = {"status": "up", "http_code": 200, "reachable": True}
    client.app.state.kafka_consumer.is_connected = True
    client.app.state.remediation_consumer.is_connected = True

    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["checks"] == {
        "kafka": True, "incident_status_kafka": True, "remediation_kafka": True, "llm": True, "rca_service": True,
    }


def test_rca_outage_and_recovery_reach_public_health_without_incident_status(client):
    with patch("ran_chatbot_service.probe_http", new_callable=AsyncMock) as probe:
        probe.return_value = {"status": "http-503", "http_code": 503, "reachable": False}
        degraded = client.get("/api/anomalies").json()
        ready_degraded = client.get("/ready").json()
        probe.return_value = {"status": "up", "http_code": 200, "reachable": True}
        recovered = client.get("/api/anomalies").json()

    assert degraded["_deps"] == {"status": "degraded", "unavailable": ["rca_service"]}
    assert degraded["anomalies"] == []
    assert ready_degraded["checks"]["rca_service"] is False
    assert recovered["_deps"] == {"status": "ok"}
    assert recovered["anomalies"] == []


def test_anomalies_empty_buffer(client):
    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["anomalies"] == []
    assert "_deps" in data


def test_anomalies_returns_most_recent_first(client, sample_anomaly):
    oldest = sample_anomaly.model_copy(update={"incident_id": "inc-001"})
    newest = sample_anomaly.model_copy(update={"incident_id": "inc-002"})
    client.app.state.recent_anomalies.append(oldest)
    client.app.state.recent_anomalies.append(newest)

    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert [a["incident_id"] for a in data["anomalies"]] == ["inc-002", "inc-001"]


def test_anomalies_includes_new_schema_fields(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)

    resp = client.get("/api/anomalies")
    data = resp.json()
    anomaly = data["anomalies"][0]
    assert "incident_id" in anomaly
    assert "zone" in anomaly
    assert "application" in anomaly
    assert "ad_label" in anomaly
    assert "ad_confidence" in anomaly
    assert "root_cause_category" in anomaly
    assert "root_cause" in anomaly
    assert "recommended_fix" in anomaly
    assert "cell_id" not in anomaly
    assert "band" not in anomaly


def test_legacy_anomaly_api_reports_missing_prediction(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)
    anomaly = client.get("/api/anomalies").json()["anomalies"][0]
    assert "ml_status" not in anomaly
    assert anomaly["ml_class"] == ""
    assert anomaly["incident_id"] == "test-001"
    assert anomaly["zone"] == "A"
    assert anomaly["application"] == "Twitch"


def test_anomalies_includes_remediation_status_fields(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)

    resp = client.get("/api/anomalies")
    anomaly = resp.json()["anomalies"][0]
    assert "remediation_status" in anomaly
    assert "remediation_job_id" in anomaly
    assert "remediation_template_name" in anomaly
    assert "remediation_job_status" in anomaly
    assert "remediation_timestamp" in anomaly
    assert "remediation_failure_stage" in anomaly
    assert "remediation_failure_reason" in anomaly


def test_anomalies_joins_remediation_result_when_matched(client, sample_anomaly, sample_remediation):
    client.app.state.recent_anomalies.append(sample_anomaly)
    client.app.state.recent_remediations.append(sample_remediation)

    resp = client.get("/api/anomalies")
    anomaly = resp.json()["anomalies"][0]
    assert anomaly["remediation_status"] == "completed"
    assert anomaly["remediation_job_id"] == "42"
    assert anomaly["remediation_template_name"] == "ran-antenna-tilt-adjust"
    assert anomaly["remediation_job_status"] == "successful"
    assert anomaly["remediation_timestamp"] == "2026-09-22T10:00:00Z"


def test_cell_failure_incident_keeps_identity_confidences_and_recovery_outcome(
    client, sample_anomaly, sample_remediation
):
    incident = sample_anomaly.model_copy(update={
        "incident_id": "antenna-001", "root_cause_category": "cell_failure",
        "ml_class": "Antenna Failure", "ml_confidence": 0.91,
    })
    recovery = {**sample_remediation, "incident_id": "antenna-001", "template_name": "ran-cell-recovery"}
    client.app.state.recent_anomalies.append(incident)
    client.app.state.recent_remediations.append(recovery)

    anomaly = client.get("/api/anomalies").json()["anomalies"][0]
    assert anomaly["incident_id"] == "antenna-001"
    assert anomaly["ml_class"] == "Antenna Failure"
    assert anomaly["ml_confidence"] == 0.91
    assert anomaly["ad_confidence"] == 0.94
    assert not {"ml_class_index", "ml_status", "ml_steer_used"} & anomaly.keys()
    assert anomaly["root_cause_category"] == "cell_failure"
    assert anomaly["remediation_template_name"] == "ran-cell-recovery"
    assert anomaly["remediation_job_status"] == "successful"
    assert anomaly["remediation_status"] == "completed"


@pytest.mark.parametrize("label", ["Co-Channel Interference (Mild)", "Co-Channel Interference (Severe)"])
def test_interference_anomalies_api_keeps_label_low_confidence_and_job(
    client, sample_anomaly, sample_remediation, label
):
    incident = sample_anomaly.model_copy(update={
        "incident_id": "interference-001", "root_cause_category": "interference",
        "ml_class": label, "ml_confidence": 0.001,
    })
    remediation = {
        **sample_remediation,
        "incident_id": "interference-001",
        "template_name": "ran-interference-mitigation",
    }
    client.app.state.recent_anomalies.append(incident)
    client.app.state.recent_remediations.append(remediation)

    anomaly = client.get("/api/anomalies").json()["anomalies"][0]
    assert anomaly["root_cause_category"] == "interference"
    assert anomaly["ml_class"] == label
    assert anomaly["ml_confidence"] == 0.001
    assert anomaly["remediation_template_name"] == "ran-interference-mitigation"
    assert anomaly["remediation_status"] == "completed"


def test_anomalies_remediation_null_when_no_match(client, sample_anomaly):
    client.app.state.recent_anomalies.append(sample_anomaly)

    resp = client.get("/api/anomalies")
    anomaly = resp.json()["anomalies"][0]
    assert anomaly["remediation_status"] is None
    assert anomaly["remediation_job_id"] is None
    assert anomaly["remediation_timestamp"] is None


def test_anomalies_remediation_failed_status(client, sample_anomaly, sample_remediation):
    failed = {**sample_remediation, "success": False, "job_status": "failed",
              "failure_stage": "aap_job", "failure_reason": "Simulated AAP job failure"}
    client.app.state.recent_anomalies.append(sample_anomaly)
    client.app.state.recent_remediations.append(failed)

    anomaly = client.get("/api/anomalies").json()["anomalies"][0]
    assert anomaly["remediation_status"] == "failed"
    assert anomaly["remediation_failure_stage"] == "aap_job"
    assert anomaly["remediation_failure_reason"] == "Simulated AAP job failure"


def test_classifier_failure_is_visible_without_enrichment_or_remediation(client):
    from ran_chatbot_service.models import IncidentStatus

    status = IncidentStatus(
        incident_id="failed-001",
        stage="rca_classification",
        outcome="failed",
        error_reason="invalid RCA class index",
        timestamp="2026-10-22T10:30:00+00:00",
        zone="A",
        application="Twitch",
        ad_label="anomalous",
        ad_confidence=0.94,
    )
    client.app.state.recent_statuses.append(status)

    response = client.get("/api/anomalies")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    incident = response.json()["anomalies"][0]
    assert incident["incident_id"] == "failed-001"
    assert incident["incident_status"] == status.model_dump()
    assert incident["root_cause_category"] == ""
    assert incident["remediation_job_id"] is None


def test_failed_status_replaces_prior_enrichment_for_same_incident(client, sample_anomaly, sample_remediation):
    from ran_chatbot_service.models import IncidentStatus

    client.app.state.recent_anomalies.append(sample_anomaly)
    client.app.state.recent_remediations.append(sample_remediation)
    client.app.state.recent_statuses.append(IncidentStatus(
        incident_id=sample_anomaly.incident_id,
        stage="rca_classification",
        outcome="failed",
        error_reason="classifier unavailable",
        timestamp="2026-10-22T10:30:00+00:00",
        zone="A",
        application="Twitch",
        ad_label="anomalous",
        ad_confidence=0.94,
    ))

    incidents = client.get("/api/anomalies").json()["anomalies"]
    assert len(incidents) == 1
    assert incidents[0]["incident_status"]["outcome"] == "failed"
    assert incidents[0]["ml_class"] == ""
    assert incidents[0]["remediation_job_id"] is None


def test_llm_failure_status_joins_enrichment_and_completed_job(client, sample_anomaly, sample_remediation):
    from ran_chatbot_service.models import IncidentStatus

    unavailable = sample_anomaly.model_copy(update={
        "root_cause_category": "cell_failure", "root_cause": "", "recommended_fix": "",
    })
    client.app.state.recent_anomalies.append(sample_anomaly)
    client.app.state.recent_anomalies.append(unavailable)
    client.app.state.recent_remediations.append({
        **sample_remediation, "template_name": "ran-cell-recovery", "job_status": "successful",
    })
    client.app.state.recent_statuses.append(IncidentStatus(
        incident_id=sample_anomaly.incident_id, stage="rca_explanation", outcome="failed",
        error_reason="LLM unavailable", timestamp="2026-10-22T10:30:00+00:00",
        zone="A", application="Twitch", ad_label="anomalous", ad_confidence=0.94,
    ))

    incidents = client.get("/api/anomalies").json()["anomalies"]
    assert len(incidents) == 1
    assert incidents[0]["root_cause_category"] == "cell_failure"
    assert incidents[0]["root_cause"] == ""
    assert incidents[0]["recommended_fix"] == ""
    assert incidents[0]["incident_status"]["error_reason"] == "LLM unavailable"
    assert incidents[0]["remediation_template_name"] == "ran-cell-recovery"
    assert incidents[0]["remediation_job_status"] == "successful"


def test_failed_and_successful_incidents_share_newest_first_order(client, sample_anomaly):
    from ran_chatbot_service.models import IncidentStatus

    client.app.state.recent_statuses.append(IncidentStatus(
        incident_id="older-failure",
        stage="rca_classification",
        outcome="failed",
        error_reason="classifier unavailable",
        timestamp="2026-10-22T10:00:00+00:00",
        zone="A",
        application="Twitch",
        ad_label="anomalous",
        ad_confidence=0.94,
    ))
    client.app.state.recent_anomalies.append(sample_anomaly.model_copy(update={
        "incident_id": "newer-success", "timestamp": "2026-10-22T10:05:00+00:00",
    }))

    incidents = client.get("/api/anomalies").json()["anomalies"]
    assert [item["incident_id"] for item in incidents] == ["newer-success", "older-failure"]


def test_anomalies_deps_includes_remediation_kafka(client):
    resp = client.get("/api/anomalies")
    deps = resp.json()["_deps"]
    assert "remediation_kafka" in str(deps) or resp.json().get("_deps", {})


def test_clear_anomalies_empties_both_buffers(client, sample_anomaly, sample_remediation):
    client.app.state.recent_anomalies.append(sample_anomaly)
    client.app.state.recent_remediations.append(sample_remediation)

    resp = client.delete("/api/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cleared"
    assert data["count"] == 0

    follow_up = client.get("/api/anomalies")
    assert follow_up.json()["count"] == 0
    assert len(client.app.state.recent_remediations) == 0


_FAKE_META = {"scenario": "antenna_failure", "incident_id": "abc12345", "zone": "A", "application": "File"}
_FAKE_BLOB = '{"incident_id": "abc12345", "zone": "A", "application": "File", "kpi_window": []}'


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_rf_filter_publishes_controlled_window(mock_publish, client):
    mock_publish.return_value = 7
    response = client.post("/api/demo/trigger", json={"scenario": "rf_filter_fault"})

    assert response.status_code == 200
    assert response.json()["scenario"] == "rf_filter_fault"
    incident_id = response.json()["incident_id"]
    published = json.loads(mock_publish.call_args.args[0])
    assert published["incident_id"] == incident_id
    assert len(published["kpi_window"]) == 128
    assert len(published["kpi_window"][0]) == 18


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_handover_publishes_controlled_window(mock_publish, client):
    mock_publish.return_value = 7
    response = client.post("/api/demo/trigger", json={"scenario": "handover_fault"})

    assert response.status_code == 200
    assert response.json()["scenario"] == "handover_fault"
    published = json.loads(mock_publish.call_args.args[0])
    assert published["incident_id"] == response.json()["incident_id"]
    assert len(published["kpi_window"]) == 128
    assert len(published["kpi_window"][0]) == 18


@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_buffer_overflow_publishes_controlled_window(mock_publish, client):
    mock_publish.return_value = 7
    response = client.post("/api/demo/trigger", json={"scenario": "buffer_overflow"})

    assert response.status_code == 200
    assert response.json()["scenario"] == "buffer_overflow"
    published = json.loads(mock_publish.call_args.args[0])
    assert published["incident_id"] == response.json()["incident_id"]
    assert len(published["kpi_window"]) == 128
    assert len(published["kpi_window"][0]) == 18


@pytest.mark.parametrize("scenario", ["high_congestion_gradual", "high_congestion_sudden"])
@patch("ran_chatbot_service.publish_demo_metrics")
def test_demo_trigger_congestion_labels_publish_controlled_windows(mock_publish, client, scenario):
    mock_publish.return_value = 7
    response = client.post("/api/demo/trigger", json={"scenario": scenario})

    assert response.status_code == 200
    assert response.json()["scenario"] == scenario
    published = json.loads(mock_publish.call_args.args[0])
    assert published["incident_id"] == response.json()["incident_id"]
    assert len(published["kpi_window"]) == 128
    assert len(published["kpi_window"][0]) == 18


@patch("ran_chatbot_service.publish_demo_metrics")
@patch("ran_chatbot_service.build_demo_sample", return_value=(_FAKE_BLOB, _FAKE_META))
def test_demo_trigger_antenna_failure(mock_build, mock_publish, client):
    mock_publish.return_value = 7
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/demo/trigger", json={"scenario": "antenna_failure"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert data["status"] == "queued"
    assert data["scenario"] == "antenna_failure"
    assert "incident_id" in data
    assert data["topic"] == "ran-combined-metrics"
    assert data["kafka_offset"] == 7
    assert "cell_id" not in data
    assert "band" not in data


@patch("ran_chatbot_service.publish_demo_metrics")
@patch("ran_chatbot_service.build_demo_sample", return_value=(_FAKE_BLOB, _FAKE_META))
def test_demo_trigger_defaults_to_antenna_failure(mock_build, mock_publish, client):
    mock_publish.return_value = 0
    resp = client.post("/api/demo/trigger", json={})
    assert resp.status_code == 200
    assert resp.json()["scenario"] == "antenna_failure"


@patch("ran_chatbot_service.publish_demo_metrics")
@patch("ran_chatbot_service.build_demo_sample", return_value=(_FAKE_BLOB, _FAKE_META))
def test_demo_trigger_kafka_failure_reported_as_502(mock_build, mock_publish, client):
    mock_publish.side_effect = Exception("Kafka unreachable")
    resp = client.post("/api/demo/trigger", json={"scenario": "antenna_failure"})
    assert resp.status_code == 502
    data = resp.json()
    assert data["status"] == "error"
    assert data["scenario"] == "antenna_failure"
    assert "incident_id" in data


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat(mock_model, client, sample_anomalies, sample_remediations):
    mock_model.return_value = ("Incident test-001 shows signal degradation in zone A.", "live")
    client.app.state.recent_anomalies.extend(sample_anomalies)
    client.app.state.recent_remediations.extend(sample_remediations)
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/chat", json={"message": "What's wrong?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["_deps"] == {"status": "ok"}
    assert "reply" in data
    assert data["model"]["source"] == "live"
    assert data["context"]["anomaly_count"] > 0
    assert data["context"]["remediation_count"] > 0


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat_model_unavailable(mock_model, client, sample_anomalies):
    mock_model.return_value = ("", "unreachable")
    client.app.state.recent_anomalies.extend(sample_anomalies)
    client.app.state.kafka_consumer.is_connected = True

    resp = client.post("/api/chat", json={"message": "Status?"})
    assert resp.status_code == 503
    data = resp.json()
    assert data["model"]["source"] == "unreachable"
    assert "error" in data


@patch("ran_chatbot_service.call_model", new_callable=AsyncMock)
def test_chat_reports_unavailable_explanation_when_model_is_down(mock_model, client, sample_anomaly):
    mock_model.return_value = ("", "unreachable")
    client.app.state.recent_anomalies.append(sample_anomaly.model_copy(update={
        "root_cause": "", "recommended_fix": "", "root_cause_category": "cell_failure",
    }))

    response = client.post("/api/chat", json={"message": "Explain test-001"})
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "ML-Derived Operational Category:\n- cell_failure" in reply
    assert "LLM Explanation:\n- unavailable" in reply
    assert "Recommended Fix:\n- unavailable" in reply


def test_chat_empty_message(client):
    resp = client.post("/api/chat", json={"message": "  "})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Please enter a question."
