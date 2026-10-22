"""Opt-in live fixture → AD → Kafka → RCA → API/chat or failure status probe.

Run this test in order: used, then disabled with an empty classify URL. Set
RCA_EVIDENCE_FILE to the same JSON path for all runs. The first run pins the
real prediction; subsequent runs must match it. Set RCA_DEPLOYMENT_NAMESPACE
for each deployment. Set RCA_EXPECTED_CLASSIFY_URL and RCA_EXPECTED_CLASSIFY_TOKEN to the
values deployed in the RCA container; leave both empty for disabled mode when
appropriate. Disabled and failed modes now stop at classification. For failure and recovery, run failed with the classify URL
still configured and its endpoint unreachable, then run used after restoring
the predictor, without restarting RCA. The test reads the running RCA
container's environment and records the order in RCA_EVIDENCE_FILE.
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import jsonschema
import pytest

pytestmark = [pytest.mark.telco, pytest.mark.integration]

ROOT = Path(__file__).resolve().parents[4]
SCENARIO = os.getenv("RCA_FIXTURE_SCENARIO", "antenna_failure")
FIXTURE = ROOT / "hub/telco-oran/src/telco_oran/fixtures" / f"{SCENARIO}.json"
REFERENCE = json.loads((ROOT / "model-serving/shared-ml-predictor/tests/fixtures/rca_reference.json").read_text())
PINNED_RCA_PREDICTION = next((case for case in REFERENCE["cases"] if case["fixture"] == FIXTURE.name), None)
if SCENARIO == "rf_filter_fault":
    # This sample has a pinned dataset label and digest; live inference must confirm the prediction.
    PINNED_RCA_PREDICTION = {
        "fixture_sha256": "df435983093170a39cad07df1434876114655efd960f1663a8a697ec13bec8e8",
        "class": "Faulty RF Filters (Temporal)",
        "class_index": 3,
    }
if SCENARIO == "handover_fault":
    PINNED_RCA_PREDICTION = {
        "fixture_sha256": "8206bffba9c9339f2ec44adf8bde2d9788a097b1624021623df836a288542328",
        "class": "Faulty Handover Algorithm (Too Frequent)",
        "class_index": 5,
    }
if SCENARIO == "buffer_overflow":
    PINNED_RCA_PREDICTION = {
        "fixture_sha256": "14e92c1db52143f2882dd8da4a1252d592cc33d2711b70b41de2df63b0a26764",
        "class": "Buffer Overflow (Gradual Buildup)",
        "class_index": 6,
    }
if SCENARIO == "resource_allocation_bugs":
    PINNED_RCA_PREDICTION = {
        "fixture_sha256": "a4f5485b8ce57ebb51d31901941c0dcc05301f3bfbd934ba782208d07b18b46b",
        "class": "Resource Allocation Bugs",
        "class_index": 7,
        "confidence": 0.9343,
    }
if SCENARIO == "high_congestion_gradual":
    PINNED_RCA_PREDICTION = {
        "fixture_sha256": "9fd76a350168d528008947351e2500d6001509d31716214bc0ba157b1970c2ae",
        "class": "High Network Congestion (Gradual Buildup)",
        "class_index": 8,
    }
TEMPLATES = {
    "antenna_misalignment": "ran-antenna-tilt-adjust",
    "interference": "ran-interference-mitigation",
    "scheduler_degradation": "ran-scheduler-optimize",
    "resource_allocation_fault": "ran-resource-allocation-correction",
    "congestion": "ran-load-balance",
    "buffer_overflow": "ran-buffer-relief",
    "capacity_exhaustion": "ran-capacity-expand",
    "cell_failure": "ran-cell-recovery",
    "rf_filter_fault": "ran-rf-filter-response",
    "doppler_shift": "ran-doppler-shift-response",
    "handover_fault": "ran-handover-stabilization",
    "unknown": "ran-generic-remediation",
}


def _rca_exec(*command: str) -> str:
    result = subprocess.run(
        [
            "oc",
            "-n",
            os.environ["RCA_DEPLOYMENT_NAMESPACE"],
            "exec",
            f"deployment/{os.environ.get('RCA_DEPLOYMENT_NAME', 'hub-ran-rca-service')}",
            "-c",
            "ran-rca-service",
            "--",
            *command,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _rca_instance() -> str:
    return _rca_exec("sh", "-c", "hostname; awk '{print $22}' /proc/1/stat")


def _publish_invalid_anomalies() -> tuple[str, str]:
    invalid_id = f"invalid-rca-{uuid4().hex}"
    malformed = f"not-json-{invalid_id}"
    script = (
        "import json,os,sys; from kafka import KafkaProducer; "
        "p=KafkaProducer(bootstrap_servers=os.environ['KAFKA_BOOTSTRAP']); "
        "topic=os.environ['KAFKA_ANOMALIES_TOPIC']; "
        "bad={'incident_id':sys.argv[1],'zone':'A','application':'File',"
        "'kpi_window':[],'ad_label':'anomalous','ad_confidence':0.9}; "
        "p.send(topic,sys.argv[2].encode()).get(timeout=10); "
        "p.send(topic,json.dumps(bad).encode()).get(timeout=10); p.close()"
    )
    _rca_exec("python", "-c", script, invalid_id, malformed)
    return invalid_id, hashlib.sha256(malformed.encode()).hexdigest()[:12]


def _wait_for_invalid_rejection(invalid_id: str, malformed_hash: str, pod: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        logs = subprocess.run(
            [
                "oc",
                "-n",
                os.environ["RCA_DEPLOYMENT_NAMESPACE"],
                "logs",
                f"pod/{pod}",
                "-c",
                "ran-rca-service",
                "--since=5m",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if (
            f"Skipping invalid RAN anomaly message incident_id={invalid_id}" in logs
            and f"Skipping malformed RAN anomaly message sha256={malformed_hash}" in logs
        ):
            return
        time.sleep(2)
    pytest.fail(f"RCA consumer did not log rejection of {invalid_id}")


def _assert_running_rca_config(mode: str) -> None:
    namespace = os.environ["RCA_DEPLOYMENT_NAMESPACE"]
    deployment = os.environ.get("RCA_DEPLOYMENT_NAME", "hub-ran-rca-service")
    result = subprocess.run(
        [
            "oc",
            "-n",
            namespace,
            "exec",
            f"deployment/{deployment}",
            "-c",
            "ran-rca-service",
            "--",
            "printenv",
            "CLASSIFY_INFERENCE_URL",
            "CLASSIFY_TOKEN",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    actual_url, actual_token = result.stdout.splitlines()
    expected_url = os.environ.get("RCA_EXPECTED_CLASSIFY_URL", "")
    assert (actual_url == expected_url) and (bool(actual_url) is (mode != "disabled"))
    assert actual_token == os.environ.get("RCA_EXPECTED_CLASSIFY_TOKEN", "")


@pytest.mark.skipif(os.getenv("RCA_REAL_E2E") != "1", reason="Requires the deployed RCA predictor")
def test_pinned_fixture_reaches_chat_in_expected_guidance_mode(ran_chatbot_client):
    assert PINNED_RCA_PREDICTION is not None, f"No reference for scenario {SCENARIO}"
    rca_url = os.environ["RAN_RCA_SERVICE_URL"]
    remediation_url = os.environ["RAN_REMEDIATION_SERVICE_URL"]
    mode = os.environ.get("RCA_EXPECTED_MODE", "used")
    assert mode in {"used", "disabled", "failed"}
    _assert_running_rca_config(mode)
    evidence_path = Path(os.environ["RCA_EVIDENCE_FILE"])
    evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {"runs": {}}
    if mode == "disabled":
        assert "used" in evidence["runs"], "Run the guided scenario first to pin the model prediction"
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == PINNED_RCA_PREDICTION["fixture_sha256"]
    fixture = json.loads(FIXTURE.read_text())
    rca_instance = _rca_instance()
    with httpx.Client(base_url=rca_url, timeout=20.0) as client:
        assert client.get("/ready").json() == {"ready": True}
    invalid_id, malformed_hash = _publish_invalid_anomalies()
    _wait_for_invalid_rejection(invalid_id, malformed_hash, rca_instance.splitlines()[0])
    trigger = ran_chatbot_client.post("/api/demo/trigger", json={"scenario": SCENARIO})
    assert trigger.status_code == 200
    incident_id = trigger.json()["incident_id"]

    deadline = time.monotonic() + 240
    anomaly = None
    while time.monotonic() < deadline:
        response = ran_chatbot_client.get("/api/anomalies")
        assert response.status_code == 200
        anomaly = next((item for item in response.json()["anomalies"] if item["incident_id"] == incident_id), None)
        if anomaly is not None:
            break
        time.sleep(5)
    assert anomaly is not None, f"incident {incident_id} did not reach anomalies API"
    assert all(
        item["incident_id"] != invalid_id for item in ran_chatbot_client.get("/api/anomalies").json()["anomalies"]
    )
    assert anomaly["zone"] == fixture["zone"]
    assert anomaly["application"] == fixture["application"]
    assert anomaly["ad_label"] == "anomalous"
    if mode in {"disabled", "failed"}:
        status = anomaly["incident_status"]
        assert status["incident_id"] == incident_id
        assert status["stage"] == "rca_classification"
        assert status["outcome"] == "failed"
        assert status["error_reason"]
        assert status["timestamp"]
        schema = json.loads((ROOT / "contracts/ran-incident-status.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(status)
        assert anomaly["ml_class"] == ""
        assert anomaly["remediation_job_id"] is None
        with httpx.Client(base_url=rca_url, timeout=20.0) as client:
            assert all(item["incident_id"] != incident_id for item in client.get("/anomalies").json()["anomalies"])
        with httpx.Client(base_url=remediation_url, timeout=20.0) as client:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                results = client.get("/remediation-results").json()["results"]
                assert all(item["incident_id"] != incident_id for item in results)
                time.sleep(2)
        evidence["runs"][mode] = {
            "incident_id": incident_id,
            "status": status,
            "rca_instance": rca_instance,
        }
        evidence.setdefault("sequence", []).append({"mode": mode, "incident_id": incident_id})
        evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
        return

    assert anomaly["kpi_window"] == fixture["kpi_window"]
    assert not {"ml_class_index", "ml_status", "ml_steer_used"} & anomaly.keys()
    confidence = anomaly["ml_confidence"]
    assert 0 < confidence < 1
    assert anomaly["ml_class"] == PINNED_RCA_PREDICTION["class"]
    if "confidence" in PINNED_RCA_PREDICTION:
        assert confidence == pytest.approx(PINNED_RCA_PREDICTION["confidence"], abs=1e-4)
    assert anomaly["root_cause_category"] in {
        "antenna_misalignment",
        "interference",
        "scheduler_degradation",
        "resource_allocation_fault",
        "congestion",
        "buffer_overflow",
        "capacity_exhaustion",
        "cell_failure",
        "rf_filter_fault",
        "doppler_shift",
        "handover_fault",
        "unknown",
    }
    assert anomaly["root_cause"]
    assert anomaly["recommended_fix"]
    if anomaly["ml_class"] == "Antenna Failure":
        assert anomaly["root_cause_category"] == "cell_failure"
    if anomaly["ml_class"] in {"Co-Channel Interference (Mild)", "Co-Channel Interference (Severe)"}:
        assert anomaly["root_cause_category"] == "interference"
    if SCENARIO == "rf_filter_fault":
        assert fixture["anomaly_class"] == anomaly["ml_class"] == "Faulty RF Filters (Temporal)"
        assert anomaly["root_cause_category"] == "rf_filter_fault"
    if SCENARIO == "doppler_shift_severe":
        assert fixture["anomaly_class"] == anomaly["ml_class"] == "Doppler Shift (Severe)"
        assert anomaly["root_cause_category"] == "doppler_shift"
    if SCENARIO == "handover_fault":
        assert fixture["anomaly_class"] == anomaly["ml_class"] == "Faulty Handover Algorithm (Too Frequent)"
        assert anomaly["root_cause_category"] == "handover_fault"
    if SCENARIO == "buffer_overflow":
        assert fixture["anomaly_class"] == anomaly["ml_class"] == "Buffer Overflow (Gradual Buildup)"
        assert anomaly["root_cause_category"] == "buffer_overflow"
    if SCENARIO == "resource_allocation_bugs":
        assert fixture["anomaly_class"] == anomaly["ml_class"] == "Resource Allocation Bugs"
        assert anomaly["root_cause_category"] == "resource_allocation_fault"
    if SCENARIO in {"high_congestion_gradual", "high_congestion_sudden"}:
        assert fixture["anomaly_class"] == anomaly["ml_class"] == PINNED_RCA_PREDICTION["class"]
        assert anomaly["root_cause_category"] == "congestion"
    schema = json.loads((ROOT / "contracts/ran-anomaly-enriched.schema.json").read_text())
    # The chatbot API receives this record only through enriched Kafka.
    jsonschema.Draft202012Validator(schema).validate(anomaly)

    with httpx.Client(base_url=rca_url, timeout=20.0) as client:
        published = client.get("/anomalies").json()["anomalies"]
    kafka_projection = next(item for item in published if item["incident_id"] == incident_id)
    assert all(item["incident_id"] != invalid_id for item in published)
    jsonschema.Draft202012Validator(schema).validate(kafka_projection)
    assert {key: kafka_projection[key] for key in ("kpi_window", "ml_class", "ml_confidence", "root_cause_category")} == {
        key: anomaly[key] for key in ("kpi_window", "ml_class", "ml_confidence", "root_cause_category")
    }

    remediation = None
    remediation_deadline = time.monotonic() + 240
    with httpx.Client(base_url=remediation_url, timeout=20.0) as client:
        while time.monotonic() < remediation_deadline:
            results = client.get("/remediation-results").json()["results"]
            remediation = next((item for item in results if item["incident_id"] == incident_id), None)
            if remediation:
                break
            time.sleep(5)
    assert remediation is not None, f"incident {incident_id} did not reach remediation"
    assert remediation["template_name"] == TEMPLATES[anomaly["root_cause_category"]]
    if anomaly["ml_class"] == "Antenna Failure":
        assert remediation["template_name"] == "ran-cell-recovery"
    if anomaly["ml_class"] in {"Co-Channel Interference (Mild)", "Co-Channel Interference (Severe)"}:
        assert remediation["template_name"] == "ran-interference-mitigation"
    if SCENARIO == "rf_filter_fault":
        assert remediation["template_name"] == "ran-rf-filter-response"
        assert remediation["job_status"] == "successful"
        assert "RF filter response" in remediation["output_summary"]
        assert "no RAN device changed" in remediation["output_summary"]
        assert "changed=0" in remediation["output_summary"]
    if SCENARIO == "doppler_shift_severe":
        assert remediation["template_name"] == "ran-doppler-shift-response"
        assert remediation["job_status"] == "successful"
        assert "Doppler frequency-tracking" in remediation["output_summary"]
        assert "no RAN device changed" in remediation["output_summary"]
        assert "changed=0" in remediation["output_summary"]
    if SCENARIO == "handover_fault":
        assert remediation["template_name"] == "ran-handover-stabilization"
        assert remediation["job_status"] == "successful"
        assert "handover stabilization" in remediation["output_summary"].lower()
        assert "no RAN device changed" in remediation["output_summary"]
        assert "changed=0" in remediation["output_summary"]
    if SCENARIO == "buffer_overflow":
        assert remediation["template_name"] == "ran-buffer-relief"
        assert remediation["job_status"] == "successful"
        assert "queue drain" in remediation["output_summary"]
        assert "simulation successful" in remediation["output_summary"]
        assert "no RAN device changed" in remediation["output_summary"]
        assert "changed=0" in remediation["output_summary"]
    if SCENARIO == "resource_allocation_bugs":
        assert remediation["template_name"] == "ran-resource-allocation-correction"
        assert remediation["job_status"] == "successful"
        assert "PRB allocation correction" in remediation["output_summary"]
        assert "simulation successful" in remediation["output_summary"]
        assert "no RAN device changed" in remediation["output_summary"]
        assert "changed=0" in remediation["output_summary"]
    if SCENARIO in {"high_congestion_gradual", "high_congestion_sudden"}:
        assert remediation["template_name"] == "ran-load-balance"
        assert remediation["job_status"] == "successful"
        assert "load balancing simulation successful" in remediation["output_summary"]
        assert "no RAN device changed" in remediation["output_summary"]
        assert "changed=0" in remediation["output_summary"]
    joined = None
    join_deadline = time.monotonic() + 60
    while time.monotonic() < join_deadline:
        joined = next(
            (item for item in ran_chatbot_client.get("/api/anomalies").json()["anomalies"]
             if item["incident_id"] == incident_id),
            None,
        )
        if joined and joined["remediation_job_status"] == remediation["job_status"]:
            break
        time.sleep(2)
    assert joined is not None
    assert joined["remediation_template_name"] == remediation["template_name"]
    assert joined["remediation_job_status"] == remediation["job_status"]
    assert joined["remediation_status"] == ("completed" if remediation["success"] else "failed")
    if SCENARIO == "rf_filter_fault":
        assert joined["ml_class"] == "Faulty RF Filters (Temporal)"
        assert joined["root_cause_category"] == "rf_filter_fault"
        assert joined["remediation_output_summary"] == remediation["output_summary"]
    if SCENARIO == "doppler_shift_severe":
        assert joined["ml_class"] == "Doppler Shift (Severe)"
        assert joined["root_cause_category"] == "doppler_shift"
        assert joined["remediation_template_name"] == "ran-doppler-shift-response"
        assert joined["remediation_output_summary"] == remediation["output_summary"]
    if SCENARIO == "handover_fault":
        assert joined["ml_class"] == "Faulty Handover Algorithm (Too Frequent)"
        assert joined["root_cause_category"] == "handover_fault"
        assert joined["remediation_template_name"] == "ran-handover-stabilization"
        assert joined["remediation_output_summary"] == remediation["output_summary"]
    if SCENARIO == "buffer_overflow":
        assert joined["incident_id"] == incident_id
        assert joined["ml_class"] == "Buffer Overflow (Gradual Buildup)"
        assert joined["root_cause_category"] == "buffer_overflow"
        assert joined["remediation_template_name"] == "ran-buffer-relief"
        assert joined["remediation_job_id"] == remediation["job_id"]
        assert joined["remediation_output_summary"] == remediation["output_summary"]
    if SCENARIO == "resource_allocation_bugs":
        assert joined["incident_id"] == incident_id
        assert joined["ml_class"] == "Resource Allocation Bugs"
        assert joined["root_cause_category"] == "resource_allocation_fault"
        assert joined["remediation_template_name"] == "ran-resource-allocation-correction"
        assert joined["remediation_job_id"] == remediation["job_id"]
        assert joined["remediation_output_summary"] == remediation["output_summary"]
    if SCENARIO in {"high_congestion_gradual", "high_congestion_sudden"}:
        assert joined["incident_id"] == incident_id
        assert joined["ml_class"] == PINNED_RCA_PREDICTION["class"]
        assert joined["root_cause_category"] == "congestion"
        assert joined["remediation_template_name"] == "ran-load-balance"
        assert joined["remediation_job_id"] == remediation["job_id"]
        assert joined["remediation_output_summary"] == remediation["output_summary"]

    chat = ran_chatbot_client.post("/api/chat", json={"message": f"Explain incident {incident_id}"})
    assert chat.status_code == 200
    reply = chat.json()["reply"]
    assert incident_id in reply
    assert "Mantis RCA Prediction" in reply
    assert "ML-Derived Operational Category" in reply
    assert anomaly["root_cause_category"] in reply
    assert ("RCA confidence" if anomaly["ml_class"] else "unavailable") in reply
    if mode == "used":
        evidence["prediction"] = {
            "class": anomaly["ml_class"],
            "class_index": PINNED_RCA_PREDICTION["class_index"],
            "confidence": anomaly["ml_confidence"],
        }
    evidence["runs"][mode] = {
        "incident_id": incident_id,
        "zone": anomaly["zone"],
        "application": anomaly["application"],
        "classify_url": os.environ.get("RCA_EXPECTED_CLASSIFY_URL", ""),
        "ml_status": mode,
        "ml_steer_used": mode == "used",
        "ml_class": anomaly["ml_class"],
        "ml_confidence": anomaly["ml_confidence"],
        "root_cause_category": anomaly["root_cause_category"],
        "root_cause": anomaly["root_cause"],
        "recommended_fix": anomaly["recommended_fix"],
        "remediation_template": remediation["template_name"],
        "chat_reply": reply,
        "rca_instance": rca_instance,
        "invalid_incident_id": invalid_id,
    }
    evidence.setdefault("sequence", []).append({"mode": mode, "incident_id": incident_id})
    if mode == "used" and len(evidence["sequence"]) >= 2 and evidence["sequence"][-2]["mode"] == "failed":
        assert rca_instance == evidence["runs"]["failed"]["rca_instance"], "RCA consumer restarted during recovery"
        evidence["recovery_confirmed"] = True
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
