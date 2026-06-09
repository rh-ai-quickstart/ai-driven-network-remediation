import pytest

REMEDIATION_STATE_FIELDS = {
    "raw_event",
    "confidence_override",
    "context_snippets",
    "root_cause_analysis",
    "decision",
    "execution_result",
    "notifications_sent",
    "awaiting_human_approval",
}


def _assert_valid_remediation_response(response, expected_raw_event, expected_decision):
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == REMEDIATION_STATE_FIELDS
    assert body["raw_event"] == expected_raw_event
    assert body["decision"] == expected_decision


class TestRemediateRouting:
    def test_high_confidence_executes(self, agent_service_client):
        response = agent_service_client.post(
            "/remediate",
            json={"raw_event": "high confidence event", "confidence_override": 0.9},
        )
        _assert_valid_remediation_response(response, "high confidence event", "execute")

    def test_mid_confidence_requests_approval(self, agent_service_client):
        response = agent_service_client.post(
            "/remediate",
            json={"raw_event": "mid confidence event", "confidence_override": 0.75},
        )
        _assert_valid_remediation_response(response, "mid confidence event", "request_approval")

    def test_low_confidence_escalates(self, agent_service_client):
        response = agent_service_client.post(
            "/remediate",
            json={"raw_event": "low confidence event", "confidence_override": 0.5},
        )
        _assert_valid_remediation_response(response, "low confidence event", "escalate")
