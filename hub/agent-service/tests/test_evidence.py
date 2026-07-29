from helpers import make_state

from agent_service.evidence import build_evidence_prompt


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

    def test_pod_logs_included_when_populated(self):
        state = make_state(pod_logs="ERROR: container OOMKilled\nRestarting pod")
        result = build_evidence_prompt(state)
        assert "Pod Logs" in result
        assert "OOMKilled" in result

    def test_pod_logs_truncated_to_last_n_lines(self):
        lines = [f"log line {i}" for i in range(200)]
        state = make_state(pod_logs="\n".join(lines))
        result = build_evidence_prompt(state)
        assert "log line 0" not in result
        assert "log line 99" not in result
        assert "log line 100" in result
        assert "log line 199" in result

    def test_log_search_results_included_when_populated(self):
        results = [{"query": "error", "hits": 5}]
        state = make_state(log_search_results=results)
        result = build_evidence_prompt(state)
        assert "Log Search Results" in result
        assert "error" in result

    def test_log_search_results_truncated_to_limit(self):
        results = [{"query": f"q-{i}"} for i in range(40)]
        state = make_state(log_search_results=results)
        result = build_evidence_prompt(state)
        assert "q-0" in result
        assert "q-19" in result
        assert "q-20" not in result
