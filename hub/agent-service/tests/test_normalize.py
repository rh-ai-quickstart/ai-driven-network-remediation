import json
from unittest.mock import patch

from agent_service.models import IncidentState
from agent_service.nodes.normalize import normalize_node

CANONICAL_EVENT = {
    "@timestamp": "2024-01-15T10:30:00Z",
    "message": "nginx CrashLoopBackOff in namespace prod",
    "level": "error",
    "kubernetes": {
        "namespace_name": "prod",
        "pod_name": "nginx-abc123",
        "container_name": "nginx",
    },
    "labels": {
        "edge_site_id": "edge-site-01",
    },
}


class TestCanonicalJsonParsing:
    def test_extracts_all_fields_from_canonical_json(self):
        raw = json.dumps(CANONICAL_EVENT)
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)
        log_event = result["log_event"]

        assert log_event.timestamp == "2024-01-15T10:30:00Z"
        assert log_event.message == "nginx CrashLoopBackOff in namespace prod"
        assert log_event.level == "error"
        assert log_event.namespace == "prod"
        assert log_event.pod_name == "nginx-abc123"
        assert log_event.container == "nginx"
        assert log_event.edge_site_id == "edge-site-01"
        assert log_event.raw == raw

    def test_missing_labels_defaults_edge_site_id_to_unknown(self):
        event = {
            "@timestamp": "2024-01-15T10:30:00Z",
            "message": "some error",
            "level": "error",
            "kubernetes": {
                "namespace_name": "prod",
                "pod_name": "nginx-abc123",
                "container_name": "nginx",
            },
        }
        raw = json.dumps(event)
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)
        log_event = result["log_event"]

        assert log_event.edge_site_id == "unknown"
        assert log_event.namespace == "prod"
        assert log_event.pod_name == "nginx-abc123"

    def test_clf_openshift_labels_edge_site_id(self):
        event = {
            "@timestamp": "2024-01-15T10:30:00Z",
            "message": "OOMKilled",
            "level": "error",
            "kubernetes": {
                "namespace_name": "dark-noc-edge",
                "pod_name": "edge-nginx-abc123-x2k9z",
                "container_name": "nginx",
            },
            "openshift": {"edge_site_id": "edge-01"},
        }
        raw = json.dumps(event)
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)
        assert result["log_event"].edge_site_id == "edge-01"

    def test_missing_kubernetes_fields_default_to_unknown(self):
        event = {
            "@timestamp": "2024-01-15T10:30:00Z",
            "message": "some error",
            "level": "warn",
            "kubernetes": {},
            "labels": {"edge_site_id": "edge-02"},
        }
        raw = json.dumps(event)
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)
        log_event = result["log_event"]

        assert log_event.namespace == "unknown"
        assert log_event.pod_name == "unknown"
        assert log_event.container == "unknown"
        assert log_event.edge_site_id == "edge-02"
        assert log_event.level == "warn"


class TestIncidentIdExtraction:
    def test_incident_id_extracted_from_canonical_json(self):
        event = {**CANONICAL_EVENT, "incident_id": "abc-123"}
        raw = json.dumps(event)
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)

        assert result["incident_id"] == "abc-123"

    def test_missing_incident_id_not_in_result(self):
        raw = json.dumps(CANONICAL_EVENT)
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)

        assert "incident_id" not in result

    def test_fallback_path_does_not_include_incident_id(self):
        state = IncidentState(raw_event="plain text")
        result = normalize_node(state)

        assert "incident_id" not in result


class TestKafkaOffsetPassthrough:
    def test_nonzero_kafka_offset_passes_through_to_log_event(self):
        raw = json.dumps(CANONICAL_EVENT)
        state = IncidentState(raw_event=raw, kafka_offset=99)
        result = normalize_node(state)
        log_event = result["log_event"]

        assert log_event.kafka_offset == 99

    def test_fallback_path_passes_kafka_offset(self):
        state = IncidentState(raw_event="not json", kafka_offset=42)
        result = normalize_node(state)

        assert result["log_event"].kafka_offset == 42


class TestNonJsonFallback:
    def test_plain_text_uses_fallback(self):
        raw = "nginx CrashLoopBackOff in namespace prod"
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)
        log_event = result["log_event"]

        assert log_event.message == raw
        assert log_event.raw == raw
        assert log_event.namespace == "unknown"
        assert log_event.pod_name == "unknown"
        assert log_event.container == "unknown"
        assert log_event.edge_site_id == "unknown"
        assert log_event.level == "unknown"
        assert log_event.timestamp == "unknown"

    def test_empty_string_uses_fallback(self):
        state = IncidentState(raw_event="")
        result = normalize_node(state)
        log_event = result["log_event"]

        assert log_event.message == ""
        assert log_event.raw == ""
        assert log_event.namespace == "unknown"
        assert log_event.pod_name == "unknown"
        assert log_event.container == "unknown"
        assert log_event.edge_site_id == "unknown"
        assert log_event.level == "unknown"
        assert log_event.timestamp == "unknown"

    def test_fallback_emits_loguru_warning(self):
        state = IncidentState(raw_event="not json")
        with patch("agent_service.nodes.normalize.logger") as mock_logger:
            normalize_node(state)
            mock_logger.warning.assert_called_once()

    def test_canonical_json_does_not_emit_warning(self):
        raw = json.dumps(CANONICAL_EVENT)
        state = IncidentState(raw_event=raw)
        with patch("agent_service.nodes.normalize.logger") as mock_logger:
            normalize_node(state)
            mock_logger.warning.assert_not_called()

    def test_broken_json_uses_fallback(self):
        raw = '{"message": "hello"'
        state = IncidentState(raw_event=raw)
        result = normalize_node(state)
        log_event = result["log_event"]

        assert log_event.message == raw
        assert log_event.raw == raw
        assert log_event.namespace == "unknown"
        assert log_event.pod_name == "unknown"
        assert log_event.container == "unknown"
        assert log_event.edge_site_id == "unknown"
        assert log_event.level == "unknown"
        assert log_event.timestamp == "unknown"


def _make_event_with_level(level: str) -> str:
    event = dict(CANONICAL_EVENT)
    event["level"] = level
    return json.dumps(event)


class TestLevelNormalization:
    def test_warning_maps_to_warn(self):
        state = IncidentState(raw_event=_make_event_with_level("WARNING"))
        result = normalize_node(state)
        assert result["log_event"].level == "warn"

    def test_lowercase_warning_maps_to_warn(self):
        state = IncidentState(raw_event=_make_event_with_level("warning"))
        result = normalize_node(state)
        assert result["log_event"].level == "warn"

    def test_mixed_case_warning_maps_to_warn(self):
        state = IncidentState(raw_event=_make_event_with_level("Warning"))
        result = normalize_node(state)
        assert result["log_event"].level == "warn"

    def test_critical_maps_to_error(self):
        state = IncidentState(raw_event=_make_event_with_level("CRITICAL"))
        result = normalize_node(state)
        assert result["log_event"].level == "error"

    def test_lowercase_critical_maps_to_error(self):
        state = IncidentState(raw_event=_make_event_with_level("critical"))
        result = normalize_node(state)
        assert result["log_event"].level == "error"

    def test_uppercase_error_is_lowercased(self):
        state = IncidentState(raw_event=_make_event_with_level("Error"))
        result = normalize_node(state)
        assert result["log_event"].level == "error"

    def test_uppercase_info_is_lowercased(self):
        state = IncidentState(raw_event=_make_event_with_level("INFO"))
        result = normalize_node(state)
        assert result["log_event"].level == "info"

    def test_already_normalized_info_passes_through(self):
        state = IncidentState(raw_event=_make_event_with_level("info"))
        result = normalize_node(state)
        assert result["log_event"].level == "info"

    def test_already_normalized_error_passes_through(self):
        state = IncidentState(raw_event=_make_event_with_level("error"))
        result = normalize_node(state)
        assert result["log_event"].level == "error"

    def test_unexpected_level_is_lowercased(self):
        state = IncidentState(raw_event=_make_event_with_level("TRACE"))
        result = normalize_node(state)
        assert result["log_event"].level == "trace"

    def test_unknown_level_passes_through(self):
        state = IncidentState(raw_event=_make_event_with_level("unknown"))
        result = normalize_node(state)
        assert result["log_event"].level == "unknown"
