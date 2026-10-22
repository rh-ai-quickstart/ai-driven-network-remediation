"""Controlled KPI window through RCA status, anomalies API, and incident card."""

import json
import shutil
import subprocess
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest
from fastapi.testclient import TestClient
from helpers import SAMPLE_ANOMALY
from ran_rca_service.graph import build_graph
from ran_rca_service.server import _handle_anomaly_message

ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "hub/ran-frontend"


@pytest.mark.parametrize(
    ("scenario", "class_index", "category"),
    [
        ("high_congestion_gradual", 8, "congestion"),
        ("high_congestion_sudden", 9, "congestion"),
        ("resource_allocation_bugs", 7, "resource_allocation_fault"),
        ("buffer_overflow", 6, "buffer_overflow"),
        ("handover_fault", 5, "handover_fault"),
        ("doppler_shift_severe", 4, "doppler_shift"),
        ("rf_filter_fault", 3, "rf_filter_fault"),
    ],
)
def test_classified_window_keeps_mantis_category_when_explanation_fails(scenario, class_index, category):
    fixture = json.loads((ROOT / "hub/telco-oran/src/telco_oran/fixtures" / f"{scenario}.json").read_text())
    assert len(fixture["kpi_window"]) == 128
    assert len(fixture["kpi_window"][0]) == 18
    anomaly = {
        **SAMPLE_ANOMALY,
        "incident_id": f"{scenario}-001",
        "zone": fixture["zone"],
        "application": fixture["application"],
        "kpi_window": fixture["kpi_window"],
    }
    classifier_response = MagicMock()
    classifier_response.json.return_value = {
        "class": fixture["anomaly_class"], "class_index": class_index, "confidence": 0.9,
    }
    classifier_client = AsyncMock()
    classifier_client.post.return_value = classifier_response
    classifier_client.__aenter__.return_value = classifier_client
    producer = MagicMock()
    with (
        patch("ran_rca_service.config.CLASSIFY_INFERENCE_URL", "https://example/v1/classify"),
        patch("ran_rca_service.nodes.classify.httpx.AsyncClient", return_value=classifier_client),
        patch("ran_rca_service.nodes.rag_retrieval._rag_client", MagicMock(search=AsyncMock(return_value=[]))),
        patch("ran_rca_service.nodes.analyze.get_llm", return_value=MagicMock(ainvoke=AsyncMock(side_effect=ConnectionError()))),
    ):
        _handle_anomaly_message(
            json.dumps(anomaly).encode(), build_graph(), producer,
            "ran-anomalies-enriched", deque(maxlen=10),
        )

    published = {call.args[0]: json.loads(call.args[1]) for call in producer.send.call_args_list}
    enriched = published["ran-anomalies-enriched"]
    assert enriched["incident_id"] == anomaly["incident_id"]
    assert enriched["ml_class"] == fixture["anomaly_class"]
    assert enriched["root_cause_category"] == category
    assert enriched["root_cause"] == enriched["recommended_fix"] == ""
    assert enriched["kpi_window"] == fixture["kpi_window"]
    schema = json.loads((ROOT / "contracts/ran-anomaly-enriched.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(enriched)


@pytest.mark.skipif(
    shutil.which("node") is None or not (FRONTEND / "node_modules/vite").exists(),
    reason="Node and installed frontend dependencies are required for the card check",
)
def test_kpi_window_classification_failure_reaches_incident_card(monkeypatch):
    classifier_response = MagicMock()
    classifier_response.json.return_value = {
        "class": "Antenna Failure",
        "class_index": 99,
        "confidence": 0.9,
    }
    classifier_client = AsyncMock()
    classifier_client.post.return_value = classifier_response
    classifier_client.__aenter__.return_value = classifier_client
    producer = MagicMock()
    enriched = deque(maxlen=10)
    with (
        patch("ran_rca_service.config.CLASSIFY_INFERENCE_URL", "https://example/v1/classify"),
        patch("ran_rca_service.nodes.classify.httpx.AsyncClient", return_value=classifier_client),
    ):
        _handle_anomaly_message(
            json.dumps(SAMPLE_ANOMALY).encode(),
            build_graph(),
            producer,
            "ran-anomalies-enriched",
            enriched,
        )

    assert not enriched
    producer.send.assert_called_once()
    assert producer.send.call_args.args[0] == "ran-incident-status"
    assert producer.send.call_args.kwargs["key"] == SAMPLE_ANOMALY["incident_id"].encode()
    status_payload = producer.send.call_args.args[1]

    monkeypatch.syspath_prepend(str(ROOT / "hub/telco-oran/src"))
    monkeypatch.syspath_prepend(str(ROOT / "hub/ran-chatbot-service/src"))
    from ran_chatbot_service import app
    from ran_chatbot_service.models import IncidentStatus

    with (
        patch("ran_chatbot_service.AnomaliesConsumer"),
        patch("ran_chatbot_service.RemediationConsumer"),
        TestClient(app) as client,
    ):
        client.app.state.recent_statuses.append(IncidentStatus.model_validate_json(status_payload))
        response = client.get("/api/anomalies")
        assert response.status_code == 200
        assert response.json()["count"] == 1
        incident = response.json()["anomalies"][0]

    rendered = subprocess.run(
        ["node", str(FRONTEND / "tests/render-incident-card.mjs")],
        cwd=FRONTEND,
        input=json.dumps(incident),
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert f"Incident {SAMPLE_ANOMALY['incident_id']}" in rendered
    assert "rca_classification: invalid RCA class index" in rendered
    assert "Job outcome:" not in rendered


@pytest.mark.skipif(
    shutil.which("node") is None or not (FRONTEND / "node_modules/vite").exists(),
    reason="Node and installed frontend dependencies are required for the card check",
)
def test_kpi_window_llm_failure_still_launches_cell_recovery_and_reaches_card_and_chat(monkeypatch):
    import jsonschema

    classifier_response = MagicMock()
    classifier_response.json.return_value = {
        "class": "Antenna Failure", "class_index": 0, "confidence": 0.9,
    }
    classifier_client = AsyncMock()
    classifier_client.post.return_value = classifier_response
    classifier_client.__aenter__.return_value = classifier_client
    rag = MagicMock()
    rag.search = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ConnectionError("private endpoint detail"))
    producer = MagicMock()
    enriched_buffer = deque(maxlen=10)
    with (
        patch("ran_rca_service.config.CLASSIFY_INFERENCE_URL", "https://example/v1/classify"),
        patch("ran_rca_service.nodes.classify.httpx.AsyncClient", return_value=classifier_client),
        patch("ran_rca_service.nodes.rag_retrieval._rag_client", rag),
        patch("ran_rca_service.nodes.analyze.get_llm", return_value=llm),
    ):
        _handle_anomaly_message(
            json.dumps(SAMPLE_ANOMALY).encode(), build_graph(), producer,
            "ran-anomalies-enriched", enriched_buffer,
        )

    published = {call.args[0]: json.loads(call.args[1]) for call in producer.send.call_args_list}
    assert set(published) == {"ran-incident-status", "ran-anomalies-enriched"}
    status = published["ran-incident-status"]
    enriched = published["ran-anomalies-enriched"]
    assert status["stage"] == "rca_explanation"
    assert status["error_reason"] == "LLM unavailable"
    assert "private endpoint detail" not in json.dumps(status)
    assert status["timestamp"]
    assert enriched["root_cause_category"] == "cell_failure"
    assert enriched["ml_class"] == "Antenna Failure"
    assert enriched["root_cause"] == enriched["recommended_fix"] == ""
    for topic, schema_name in (
        ("ran-incident-status", "ran-incident-status.schema.json"),
        ("ran-anomalies-enriched", "ran-anomaly-enriched.schema.json"),
    ):
        schema = json.loads((ROOT / "contracts" / schema_name).read_text())
        jsonschema.Draft202012Validator(schema).validate(published[topic])

    monkeypatch.syspath_prepend(str(ROOT / "hub/telco-oran/src"))
    monkeypatch.syspath_prepend(str(ROOT / "hub/ran-chatbot-service/src"))
    from ran_chatbot_service import app
    from ran_chatbot_service.chat import format_chat_reply
    from ran_chatbot_service.models import EnrichedAnomaly, IncidentStatus

    with (
        patch("ran_chatbot_service.AnomaliesConsumer"),
        patch("ran_chatbot_service.RemediationConsumer"),
        TestClient(app) as client,
    ):
        client.app.state.recent_anomalies.append(EnrichedAnomaly.model_validate(enriched))
        client.app.state.recent_statuses.append(IncidentStatus.model_validate(status))
        client.app.state.recent_remediations.append({
            "incident_id": enriched["incident_id"], "template_name": "ran-cell-recovery",
            "job_id": "42", "job_status": "successful", "success": True,
        })
        response = client.get("/api/anomalies")
        assert response.status_code == 200
        assert response.json()["count"] == 1
        incident = response.json()["anomalies"][0]

    assert incident["root_cause"] == incident["recommended_fix"] == ""
    assert incident["incident_status"]["stage"] == "rca_explanation"
    assert incident["remediation_template_name"] == "ran-cell-recovery"
    assert incident["remediation_job_status"] == "successful"
    rendered = subprocess.run(
        ["node", str(FRONTEND / "tests/render-incident-card.mjs")],
        cwd=FRONTEND, input=json.dumps(incident), text=True, capture_output=True, check=True,
    ).stdout
    assert "rca_explanation: LLM unavailable" in rendered
    assert "Job outcome: successful" in rendered
    assert "LLM Explanation</span><p>unavailable</p>" in rendered
    assert "Recommended Fix</span><p>unavailable</p>" in rendered
    reply = format_chat_reply("What happened to test-001?", "invented explanation", [EnrichedAnomaly.model_validate(enriched)])
    assert "LLM Explanation:\n- unavailable" in reply
    assert "Recommended Fix:\n- unavailable" in reply
    assert "invented explanation" not in reply
