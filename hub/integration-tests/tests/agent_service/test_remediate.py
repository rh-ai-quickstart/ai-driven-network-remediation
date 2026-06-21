INCIDENT_STATE_FIELDS = {
    "raw_event",
    "log_event",
    "incident_id",
    "incident_start_ms",
    "confidence_override",
    "failure_type_override",
    "context_snippets",
    "rag_query_used",
    "root_cause_analysis",
    "analysis_tokens_used",
    "analysis_latency_ms",
    "decision",
    "remediation_result",
    "pod_status",
    "recent_errors",
    "slack_thread_ts",
    "servicenow_ticket",
    "langfuse_trace_id",
    "total_duration_ms",
    "error_message",
}


def _assert_valid_remediation_response(response, expected_raw_event, expected_decision):
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == INCIDENT_STATE_FIELDS
    assert body["raw_event"] == expected_raw_event
    assert body["decision"] == expected_decision


class TestRemediateRouting:
    def test_high_confidence_remediates(self, agent_service_client):
        response = agent_service_client.post(
            "/remediate",
            json={"raw_event": "high confidence event", "confidence_override": 0.9},
        )
        _assert_valid_remediation_response(response, "high confidence event", "remediate")

    def test_high_confidence_generation_type_routes_to_lightspeed(self, agent_service_client):
        response = agent_service_client.post(
            "/remediate",
            json={
                "raw_event": "high confidence event",
                "confidence_override": 0.9,
                "failure_type_override": "KafkaLag",
            },
        )
        _assert_valid_remediation_response(response, "high confidence event", "lightspeed")

    def test_mid_confidence_escalates(self, agent_service_client):
        response = agent_service_client.post(
            "/remediate",
            json={"raw_event": "mid confidence event", "confidence_override": 0.75},
        )
        _assert_valid_remediation_response(response, "mid confidence event", "escalate")

    def test_low_confidence_escalates(self, agent_service_client):
        response = agent_service_client.post(
            "/remediate",
            json={"raw_event": "low confidence event", "confidence_override": 0.5},
        )
        _assert_valid_remediation_response(response, "low confidence event", "escalate")
