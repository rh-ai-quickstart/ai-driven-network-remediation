import pytest
from kafka.errors import KafkaError, NoBrokersAvailable
from mcp_kafka.utils import _resolve_topic, format_error


class TestResolveTopic:
    def test_allowed_topic_passes(self):
        result = _resolve_topic("noc-alerts", ["noc-alerts", "system-alerts"], "consume")
        assert result == "noc-alerts"

    def test_empty_allow_list_passes(self):
        result = _resolve_topic("any-topic", [], "consume")
        assert result == "any-topic"

    def test_disallowed_topic_raises(self):
        with pytest.raises(ValueError, match="not allowed for produce"):
            _resolve_topic("bad-topic", ["noc-alerts"], "produce")

    def test_suggestions_in_error(self):
        with pytest.raises(ValueError, match="Did you mean"):
            _resolve_topic("noc", ["noc-alerts", "system-alerts"], "consume")

    def test_no_suggestions_no_did_you_mean(self):
        with pytest.raises(ValueError) as exc_info:
            _resolve_topic("zzzzz", ["noc-alerts"], "consume")
        assert "Did you mean" not in str(exc_info.value)


class TestFormatError:
    def test_no_brokers_available(self):
        result = format_error(NoBrokersAvailable())
        assert result["success"] is False
        assert result["error"] == "connection_error"
        assert "Cannot connect" in result["message"]

    def test_value_error(self):
        result = format_error(ValueError("bad input"))
        assert result["success"] is False
        assert result["error"] == "validation_error"
        assert result["message"] == "bad input"

    def test_kafka_error(self):
        result = format_error(KafkaError("something broke"))
        assert result["success"] is False
        assert result["error"] == "kafka_error"

    def test_unexpected_error(self):
        result = format_error(RuntimeError("oops"))
        assert result["success"] is False
        assert result["error"] == "unexpected_error"
        assert result["message"] == "oops"

    def test_suggestions_included(self):
        result = format_error(ValueError("not found"), suggestions=["noc-alerts"])
        assert result["suggestions"] == ["noc-alerts"]

    def test_suggestions_default_empty(self):
        result = format_error(ValueError("err"))
        assert result["suggestions"] == []
