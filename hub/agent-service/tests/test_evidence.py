from helpers import make_state

from agent_service.evidence import (
    build_evidence_prompt,
    build_grounding_text,
    get_pod_logs_for_attachment,
    get_structured_attachments,
)


class TestBuildEvidencePrompt:
    def test_empty_fields_returns_empty_string(self):
        state = make_state()
        result = build_evidence_prompt(state)
        assert result == ""

    def test_pod_status_included_when_populated(self):
        state = make_state(pod_status={"items": [{"metadata": {"name": "nginx-abc"}}]})
        attachments = get_structured_attachments(state)
        assert any("nginx-abc" in a["content"] for a in attachments)

    def test_cluster_events_included_when_populated(self):
        events = [{"reason": "OOMKilled", "message": "container killed"}]
        state = make_state(cluster_events=events)
        attachments = get_structured_attachments(state)
        assert any("OOMKilled" in a["content"] for a in attachments)

    def test_recent_errors_included_when_populated(self):
        errors = [{"pattern": "segfault", "count": 5}]
        state = make_state(recent_errors=errors)
        result = build_evidence_prompt(state)
        assert "Recent Errors" in result
        assert "segfault" in result

    def test_multiple_sections_combined(self):
        state = make_state(
            resource_specs="Deployment/myapp",
            recent_errors=[{"pattern": "timeout"}],
            log_search_results=[{"query": "error"}],
        )
        result = build_evidence_prompt(state)
        assert "Resource Configuration" in result
        assert "Recent Errors" in result
        assert "Log Search Results" in result

    def test_cluster_events_truncated_to_limit(self):
        events = [{"reason": f"event-{i}"} for i in range(100)]
        state = make_state(cluster_events=events)
        content = "".join(a["content"] for a in get_structured_attachments(state))
        assert "event-0" in content
        assert "event-14" in content
        assert "event-15" not in content

    def test_pod_logs_included_when_populated(self):
        state = make_state(pod_logs="ERROR: container OOMKilled\nRestarting pod")
        result = get_pod_logs_for_attachment(state)
        assert "OOMKilled" in result

    def test_pod_logs_truncated_to_last_n_lines(self):
        lines = [f"log line {i}" for i in range(200)]
        state = make_state(pod_logs="\n".join(lines))
        result = get_pod_logs_for_attachment(state)
        assert "log line 169" not in result
        assert "log line 170" in result
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
        assert "q-9" in result
        assert "q-10" not in result

    def test_resource_specs_included_when_populated(self):
        spec_text = "Deployment/myapp\n  replicas: 3\n  cpu: 500m"
        state = make_state(resource_specs=spec_text)
        result = build_evidence_prompt(state)
        assert "Resource Configuration" in result
        assert "replicas: 3" in result
        assert "cpu: 500m" in result


class TestDictPodStatusNotSliced:
    """pod_status is a dict; slicing it like a list raised KeyError."""

    def test_build_grounding_text_handles_dict_pod_status(self):
        state = make_state(pod_status={"namespace": "edge-site-01", "pods": [{"name": "memory-hog"}]})
        result = build_grounding_text(state)
        assert "memory-hog" in result
        assert "edge-site-01" in result

    def test_get_structured_attachments_handles_dict_pod_status(self):
        state = make_state(pod_status={"namespace": "edge-site-01", "pods": [{"name": "memory-hog"}]})
        attachments = get_structured_attachments(state)
        assert any("memory-hog" in a["content"] for a in attachments)
