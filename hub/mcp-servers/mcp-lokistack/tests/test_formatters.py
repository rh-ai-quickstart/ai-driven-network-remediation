from mcp_lokistack.formatters import (
    _normalize_message,
    format_log_streams,
    format_metric_series,
    group_error_patterns,
)

from .conftest import SAMPLE_MATRIX_RESPONSE, SAMPLE_STREAMS_RESPONSE


class TestFormatLogStreams:
    def test_basic(self):
        result = format_log_streams(SAMPLE_STREAMS_RESPONSE, limit=10)
        assert len(result) == 3
        assert result[0]["line"] == "error: connection refused"
        assert "timestamp" in result[0]
        assert result[0]["labels"] == {
            "namespace": "dark-noc-edge",
            "app": "nginx",
        }

    def test_sorted_descending(self):
        result = format_log_streams(SAMPLE_STREAMS_RESPONSE, limit=10)
        timestamps = [r["timestamp"] for r in result]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_respects_limit(self):
        result = format_log_streams(SAMPLE_STREAMS_RESPONSE, limit=2)
        assert len(result) == 2

    def test_empty(self):
        result = format_log_streams({"data": {"result": []}}, limit=10)
        assert result == []

    def test_structured_json_includes_fields(self):
        data = {
            "data": {
                "result": [
                    {
                        "stream": {"app": "test"},
                        "values": [
                            [
                                "1716710400000000000",
                                '{"message": "something failed", "level": "error", "code": 500}',
                            ],
                        ],
                    },
                ],
            },
        }
        result = format_log_streams(data, limit=10)
        assert result[0]["line"] == "something failed"
        assert result[0]["fields"] == {"message": "something failed", "level": "error", "code": 500}

    def test_plain_text_no_fields(self):
        result = format_log_streams(SAMPLE_STREAMS_RESPONSE, limit=10)
        assert "fields" not in result[0]


class TestFormatMetricSeries:
    def test_basic(self):
        result = format_metric_series(SAMPLE_MATRIX_RESPONSE)
        assert len(result) == 3
        assert result[0]["value"] == 5
        assert result[1]["value"] == 12
        assert "time" in result[0]

    def test_includes_labels(self):
        result = format_metric_series(SAMPLE_MATRIX_RESPONSE)
        assert result[0]["labels"] == {"kubernetes_namespace_name": "dark-noc-edge"}

    def test_empty(self):
        result = format_metric_series({"data": {"result": []}})
        assert result == []


class TestNormalizeMessage:
    def test_uuid(self):
        msg = "error for pod a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert "<UUID>" in _normalize_message(msg)

    def test_ip(self):
        msg = "connection from 192.168.1.100 failed"
        assert "<IP>" in _normalize_message(msg)

    def test_long_numeric_id(self):
        msg = "request 123456 failed"
        assert "<ID>" in _normalize_message(msg)

    def test_short_numeric_id_kept(self):
        msg = "request 12345 failed"
        assert "12345" in _normalize_message(msg)

    def test_iso_timestamp(self):
        msg = "2024-01-15T10:30:00Z error occurred"
        result = _normalize_message(msg)
        assert "2024-01-15" not in result

    def test_syslog_timestamp(self):
        msg = "Jan 15 10:30:00 error occurred"
        result = _normalize_message(msg)
        assert "Jan 15" not in result


class TestGroupErrorPatterns:
    def test_basic_grouping(self):
        entries = [
            {"line": "error: connection refused", "timestamp": "t1"},
            {"line": "error: connection refused", "timestamp": "t2"},
            {"line": "error: timeout", "timestamp": "t3"},
        ]
        result = group_error_patterns(entries, top_n=10)
        assert len(result) == 2
        assert result[0]["count"] == 2
        assert result[0]["percentage"] > 0

    def test_normalization_groups(self):
        entries = [
            {
                "line": "error for 192.168.1.1 on pod 123456",
                "timestamp": "t1",
            },
            {
                "line": "error for 10.0.0.1 on pod 789012",
                "timestamp": "t2",
            },
        ]
        result = group_error_patterns(entries, top_n=10)
        assert len(result) == 1
        assert result[0]["count"] == 2

    def test_top_n(self):
        entries = [{"line": f"error type {i}", "timestamp": f"t{i}"} for i in range(20)]
        result = group_error_patterns(entries, top_n=5)
        assert len(result) == 5

    def test_empty(self):
        result = group_error_patterns([], top_n=10)
        assert result == []

    def test_timestamps_tracked(self):
        entries = [
            {"line": "error: x", "timestamp": "2024-01-01T00:00:00Z"},
            {"line": "error: x", "timestamp": "2024-01-02T00:00:00Z"},
            {"line": "error: x", "timestamp": "2024-01-03T00:00:00Z"},
        ]
        result = group_error_patterns(entries, top_n=10)
        assert result[0]["first_seen"] == "2024-01-01T00:00:00Z"
        assert result[0]["last_seen"] == "2024-01-03T00:00:00Z"

    def test_timestamps_out_of_order(self):
        entries = [
            {"line": "error: x", "timestamp": "2024-01-02T00:00:00Z"},
            {"line": "error: x", "timestamp": "2024-01-01T00:00:00Z"},
            {"line": "error: x", "timestamp": "2024-01-03T00:00:00Z"},
        ]
        result = group_error_patterns(entries, top_n=10)
        assert result[0]["first_seen"] == "2024-01-01T00:00:00Z"
        assert result[0]["last_seen"] == "2024-01-03T00:00:00Z"
