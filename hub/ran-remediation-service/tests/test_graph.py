"""Integration tests for the full LangGraph pipeline."""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch
from unittest.mock import patch as sync_patch

import pytest
from ran_remediation_service.aap_client import AAPError
from ran_remediation_service.graph import build_graph
from ran_remediation_service.models import RemediationState
from ran_remediation_service.nodes.audit import (
    audit_node,
    build_audit_payload,
    publish_remediation_record,
)

_ANTENNA_SAMPLE = {
    "incident_id": "a3f7c2d1",
    "zone": "A",
    "application": "File",
    "ad_label": "anomalous",
    "ad_confidence": 0.9995,
    "root_cause_category": "antenna_misalignment",
    "root_cause": "Signal degradation consistent with antenna misalignment.",
    "recommended_fix": "Adjust antenna tilt per vendor guide Section 4.3.2.",
}

_OUTAGE_SAMPLE = {
    "incident_id": "b9e1f4a2",
    "zone": "C",
    "application": "Twitch",
    "ad_label": "anomalous",
    "ad_confidence": 0.9999,
    "root_cause_category": "cell_failure",
    "root_cause": "Cell failure detected — outage and restart required.",
    "recommended_fix": "Run cell recovery procedure.",
}

_GENERIC_SAMPLE = {
    "incident_id": "c0d2e5b3",
    "zone": "B",
    "application": "YouTube",
    "ad_label": "anomalous",
    "ad_confidence": 0.88,
    "root_cause_category": "unknown",
    "root_cause": "Undetermined anomaly — no specific pattern matched.",
    "recommended_fix": "Check system logs.",
}


@contextmanager
def _patch_aap(job_id: int, output: str):
    """Patch the aap_client calls for a job that launches and succeeds at once."""
    values = {
        "launch_job": job_id,
        "get_job_status": {
            "status": "successful",
            "failed": False,
            "elapsed": 1.0,
            "finished": "2026-09-21T10:00:00Z",
        },
        "get_job_output": output,
    }
    with ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(
                patch(f"ran_remediation_service.nodes.remediate.{name}", new_callable=AsyncMock, return_value=value)
            )
        yield


@pytest.mark.asyncio
async def test_graph_successful_remediation():
    graph = build_graph()

    with (
        _patch_aap(10, "ok=2 changed=1"),
        patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0),
        patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
    ):
        result = await graph.ainvoke(_ANTENNA_SAMPLE)

    assert result["template_name"] == "ran-antenna-tilt-adjust"
    assert result["success"] is True
    assert result["job_id"] == "10"


@pytest.mark.asyncio
async def test_graph_cell_outage_routes_to_recovery():
    graph = build_graph()

    with (
        _patch_aap(20, "cell recovered"),
        patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0),
        patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
    ):
        result = await graph.ainvoke(_OUTAGE_SAMPLE)

    assert result["template_name"] == "ran-cell-recovery"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_graph_launches_cell_recovery_when_llm_explanation_is_unavailable():
    graph = build_graph()
    enriched = {**_OUTAGE_SAMPLE, "root_cause": "", "recommended_fix": ""}
    with (
        patch("ran_remediation_service.nodes.remediate.launch_job", new_callable=AsyncMock, return_value=42) as launch,
        patch("ran_remediation_service.nodes.remediate.get_job_status", new_callable=AsyncMock, return_value={
            "status": "successful", "failed": False, "elapsed": 1.0, "finished": "2026-10-22T10:31:00Z",
        }),
        patch("ran_remediation_service.nodes.remediate.get_job_output", new_callable=AsyncMock, return_value="cell recovered"),
        patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0),
        patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
    ):
        result = await graph.ainvoke(enriched)

    assert result["template_name"] == "ran-cell-recovery"
    assert result["job_status"] == "successful"
    assert result["success"] is True
    launch.assert_awaited_once()
    assert launch.await_args.args[0] == "ran-cell-recovery"
    assert launch.await_args.args[1]["root_cause"] == ""
    assert launch.await_args.args[1]["recommended_fix"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["Co-Channel Interference (Mild)", "Co-Channel Interference (Severe)"])
async def test_graph_launches_interference_mock_job_for_either_low_confidence_label(label):
    graph = build_graph()
    enriched = {
        **_ANTENNA_SAMPLE,
        "root_cause_category": "interference",
        "ml_class": label,
        "ml_confidence": 0.001,
    }
    with (
        patch("ran_remediation_service.nodes.remediate.launch_job", new_callable=AsyncMock, return_value=21) as launch,
        patch("ran_remediation_service.nodes.remediate.get_job_status", new_callable=AsyncMock, return_value={
            "status": "successful", "failed": False, "elapsed": 1.0, "finished": "2026-09-21T10:00:00Z",
        }),
        patch("ran_remediation_service.nodes.remediate.get_job_output", new_callable=AsyncMock, return_value="ok"),
        patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0),
        patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
    ):
        result = await graph.ainvoke(enriched)

    assert result["template_name"] == "ran-interference-mitigation"
    assert result["success"] is True
    launch.assert_awaited_once()
    assert launch.await_args.args[0] == "ran-interference-mitigation"


@pytest.mark.asyncio
async def test_graph_unknown_root_cause_uses_generic_template():
    graph = build_graph()

    with (
        _patch_aap(30, "generic ok"),
        patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0),
        patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
    ):
        result = await graph.ainvoke(_GENERIC_SAMPLE)

    assert result["template_name"] == "ran-generic-remediation"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_graph_failed_remediation_still_audits():
    graph = build_graph()
    audited = []

    with (
        patch(
            "ran_remediation_service.nodes.remediate.launch_job",
            new_callable=AsyncMock,
            side_effect=AAPError("template not found"),
        ),
        patch(
            "ran_remediation_service.nodes.audit.publish_remediation_record",
            side_effect=lambda p, **kw: audited.append(p) or 0,
        ),
        patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False),
    ):
        result = await graph.ainvoke(_ANTENNA_SAMPLE)

    assert result["success"] is False
    assert len(audited) == 1
    assert audited[0]["success"] is False
    assert audited[0]["incident_id"] == "a3f7c2d1"


# ── Audit publish coverage ──────────────────────────────────────────────────


def _audit_state():
    return RemediationState(
        incident_id="a3f7c2d1",
        zone="A",
        application="File",
        ad_label="anomalous",
        ad_confidence=0.9995,
        root_cause="antenna misalignment",
        template_name="ran-antenna-tilt-adjust",
        job_id="7",
        job_status="successful",
        success=True,
        output_summary="ok=3",
    )


def test_build_audit_payload_fields():
    state = _audit_state()
    payload = build_audit_payload(state)
    assert payload["incident_id"] == "a3f7c2d1"
    assert payload["zone"] == "A"
    assert payload["application"] == "File"
    assert payload["success"] is True
    assert payload["template_name"] == "ran-antenna-tilt-adjust"


def test_publish_remediation_record_calls_kafka():
    mock_producer = MagicMock()
    mock_future = MagicMock()
    mock_future.get.return_value = MagicMock(offset=5)
    mock_producer.send.return_value = mock_future

    with sync_patch("ran_remediation_service.nodes.audit.KafkaProducer", return_value=mock_producer):
        offset = publish_remediation_record(
            {"incident_id": "x", "success": True},
            bootstrap_servers="localhost:9092",
            topic="ran-remediation-results",
        )

    assert offset == 5
    mock_producer.send.assert_called_once()
    mock_producer.close.assert_called_once()


def test_audit_node_kafka_failure_propagates():
    state = _audit_state()
    with sync_patch(
        "ran_remediation_service.nodes.audit.publish_remediation_record", side_effect=Exception("kafka down")
    ):
        with pytest.raises(Exception, match="kafka down"):
            audit_node(state)
