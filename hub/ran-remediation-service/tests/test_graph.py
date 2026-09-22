"""Integration tests for the full LangGraph pipeline."""

from unittest.mock import AsyncMock, patch

import pytest

from ran_remediation_service.graph import build_graph

_ANTENNA_SAMPLE = {
    "incident_id":     "a3f7c2d1",
    "zone":            "A",
    "application":     "File",
    "ad_label":        "anomalous",
    "ad_confidence":   0.9995,
    "root_cause":      "Signal degradation consistent with antenna misalignment.",
    "recommended_fix": "Adjust antenna tilt per vendor guide Section 4.3.2.",
}

_OUTAGE_SAMPLE = {
    "incident_id":     "b9e1f4a2",
    "zone":            "C",
    "application":     "Twitch",
    "ad_label":        "anomalous",
    "ad_confidence":   0.9999,
    "root_cause":      "Cell failure detected — outage and restart required.",
    "recommended_fix": "Run cell recovery procedure.",
}

_GENERIC_SAMPLE = {
    "incident_id":     "c0d2e5b3",
    "zone":            "B",
    "application":     "YouTube",
    "ad_label":        "anomalous",
    "ad_confidence":   0.88,
    "root_cause":      "Undetermined anomaly — no specific pattern matched.",
    "recommended_fix": "Check system logs.",
}


@pytest.mark.asyncio
async def test_graph_successful_remediation():
    graph = build_graph()

    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool, \
         patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0), \
         patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False):
        mock_tool.side_effect = [
            {"success": True, "job_id": 10},
            {"status": "successful", "failed": False, "elapsed": 1.0, "finished": "2026-09-21T10:00:00Z"},
            {"output": "ok=2 changed=1"},
        ]
        result = await graph.ainvoke(_ANTENNA_SAMPLE)

    assert result["template_name"] == "ran-antenna-tilt-adjust"
    assert result["success"] is True
    assert result["job_id"] == "10"


@pytest.mark.asyncio
async def test_graph_cell_outage_routes_to_recovery():
    graph = build_graph()

    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool, \
         patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0), \
         patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False):
        mock_tool.side_effect = [
            {"success": True, "job_id": 20},
            {"status": "successful", "failed": False, "elapsed": 2.0, "finished": "2026-09-21T10:00:00Z"},
            {"output": "cell recovered"},
        ]
        result = await graph.ainvoke(_OUTAGE_SAMPLE)

    assert result["template_name"] == "ran-cell-recovery"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_graph_unknown_root_cause_uses_generic_template():
    graph = build_graph()

    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool, \
         patch("ran_remediation_service.nodes.audit.publish_remediation_record", return_value=0), \
         patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False):
        mock_tool.side_effect = [
            {"success": True, "job_id": 30},
            {"status": "successful", "failed": False, "elapsed": 1.0, "finished": "2026-09-21T10:00:00Z"},
            {"output": "generic ok"},
        ]
        result = await graph.ainvoke(_GENERIC_SAMPLE)

    assert result["template_name"] == "ran-generic-remediation"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_graph_failed_remediation_still_audits():
    graph = build_graph()
    audited = []

    with patch("ran_remediation_service.nodes.remediate.invoke_tool", new_callable=AsyncMock) as mock_tool, \
         patch("ran_remediation_service.nodes.audit.publish_remediation_record", side_effect=lambda p, **kw: audited.append(p) or 0), \
         patch("ran_remediation_service.nodes.notify.SLACK_ENABLED", False):
        mock_tool.return_value = {"success": False, "error": "template not found"}
        result = await graph.ainvoke(_ANTENNA_SAMPLE)

    assert result["success"] is False
    assert len(audited) == 1
    assert audited[0]["success"] is False
    assert audited[0]["incident_id"] == "a3f7c2d1"


# ── Audit publish coverage ──────────────────────────────────────────────────

from unittest.mock import MagicMock, patch as sync_patch
from ran_remediation_service.nodes.audit import build_audit_payload, publish_remediation_record, audit_node
from ran_remediation_service.models import RemediationState


def _audit_state():
    return RemediationState(
        incident_id="a3f7c2d1", zone="A", application="File",
        ad_label="anomalous", ad_confidence=0.9995,
        root_cause="antenna misalignment",
        template_name="ran-antenna-tilt-adjust",
        job_id="7", job_status="successful",
        success=True, output_summary="ok=3",
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


def test_audit_node_kafka_failure_does_not_raise():
    state = _audit_state()
    with sync_patch("ran_remediation_service.nodes.audit.publish_remediation_record", side_effect=Exception("kafka down")):
        result = audit_node(state)
    assert "total_duration_ms" in result


