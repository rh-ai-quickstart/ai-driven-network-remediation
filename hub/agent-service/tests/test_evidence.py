from agent_service.evidence import build_evidence_prompt
from helpers import make_state


class TestBuildEvidencePrompt:
    def test_empty_fields_returns_empty_string(self):
        state = make_state()
        result = build_evidence_prompt(state)
        assert result == ""

    def test_pod_status_included_when_populated(self):
        state = make_state(pod_status={"items": [{"metadata": {"name": "nginx-abc"}}]})
        result = build_evidence_prompt(state)
        assert "Pod Status" in result
        assert "nginx-abc" in result

    def test_cluster_events_included_when_populated(self):
        events = [{"reason": "OOMKilled", "message": "container killed"}]
        state = make_state(cluster_events=events)
        result = build_evidence_prompt(state)
        assert "Cluster Events" in result
        assert "OOMKilled" in result

    def test_recent_errors_included_when_populated(self):
        errors = [{"pattern": "segfault", "count": 5}]
        state = make_state(recent_errors=errors)
        result = build_evidence_prompt(state)
        assert "Recent Errors" in result
        assert "segfault" in result

    def test_multiple_sections_combined(self):
        state = make_state(
            pod_status={"items": []},
            cluster_events=[{"reason": "Pulled"}],
            recent_errors=[{"pattern": "timeout"}],
        )
        result = build_evidence_prompt(state)
        assert "Pod Status" in result
        assert "Cluster Events" in result
        assert "Recent Errors" in result

    def test_cluster_events_truncated_to_limit(self):
        events = [{"reason": f"event-{i}"} for i in range(100)]
        state = make_state(cluster_events=events)
        result = build_evidence_prompt(state)
        assert "event-0" in result
        assert "event-99" not in result
