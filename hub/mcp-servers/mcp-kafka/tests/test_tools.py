from unittest.mock import MagicMock, patch

from kafka.errors import NoBrokersAvailable
from kafka.structs import TopicPartition
from mcp_kafka.tools import (
    consume_topic,
    get_consumer_lag,
    list_topics,
    produce_message,
)


@patch("mcp_kafka.tools.KafkaConsumer")
class TestListTopics:
    def test_success(self, mock_consumer_cls):
        consumer = mock_consumer_cls.return_value
        consumer.topics.return_value = {"noc-alerts", "system-alerts", "_consumer_offsets"}
        consumer.partitions_for_topic.return_value = {0}

        result = list_topics()

        assert result["success"] is True
        assert result["count"] == 2
        names = [t["name"] for t in result["topics"]]
        assert "_consumer_offsets" not in names
        assert "noc-alerts" in names
        consumer.close.assert_called_once()

    def test_connection_error(self, mock_consumer_cls):
        mock_consumer_cls.side_effect = NoBrokersAvailable()

        result = list_topics()

        assert result["success"] is False
        assert result["error"] == "connection_error"


@patch("mcp_kafka.tools.KafkaConsumer")
class TestConsumeTopic:
    def _make_msg(self, partition=0, offset=0, timestamp=1700000000000, value='{"level":"error"}'):
        msg = MagicMock()
        msg.partition = partition
        msg.offset = offset
        msg.timestamp = timestamp
        msg.value = value
        return msg

    def test_success(self, mock_consumer_cls):
        consumer = mock_consumer_cls.return_value
        tp = TopicPartition("system-alerts", 0)
        consumer.partitions_for_topic.return_value = {0}
        consumer.end_offsets.return_value = {tp: 50}
        consumer.poll.return_value = {tp: [self._make_msg()]}

        result = consume_topic(topic="system-alerts", max_messages=5)

        assert result["success"] is True
        assert result["topic"] == "system-alerts"
        consumer.assign.assert_called_once()
        consumer.close.assert_called_once()

    def test_non_json_message(self, mock_consumer_cls):
        consumer = mock_consumer_cls.return_value
        tp = TopicPartition("system-alerts", 0)
        consumer.partitions_for_topic.return_value = {0}
        consumer.end_offsets.return_value = {tp: 50}
        consumer.poll.return_value = {tp: [self._make_msg(value="plain text not json")]}

        result = consume_topic(topic="system-alerts", max_messages=5)

        assert result["success"] is True
        assert result["messages"][0]["value"] == "plain text not json"

    def test_max_messages_break(self, mock_consumer_cls):
        consumer = mock_consumer_cls.return_value
        tp = TopicPartition("system-alerts", 0)
        consumer.partitions_for_topic.return_value = {0}
        consumer.end_offsets.return_value = {tp: 50}
        msgs = [self._make_msg(offset=i, value='{"i":' + str(i) + "}") for i in range(10)]
        consumer.poll.return_value = {tp: msgs}

        result = consume_topic(topic="system-alerts", max_messages=3)

        assert result["success"] is True
        assert result["count"] == 3

    def test_clamping_reported(self, mock_consumer_cls):
        consumer = mock_consumer_cls.return_value
        tp = TopicPartition("system-alerts", 0)
        consumer.partitions_for_topic.return_value = {0}
        consumer.end_offsets.return_value = {tp: 50}
        consumer.poll.return_value = {}

        result = consume_topic(topic="system-alerts", max_messages=500, timeout_ms=30000)

        assert result["success"] is True
        assert result["clamped"]["max_messages"] == 100
        assert result["clamped"]["timeout_ms"] == 15000

    def test_topic_not_allowed(self, mock_consumer_cls):
        result = consume_topic(topic="secret-topic")

        assert result["success"] is False
        assert result["error"] == "validation_error"
        assert "not allowed for consume" in result["message"]

    def test_topic_not_found_with_suggestions(self, mock_consumer_cls):
        consumer = mock_consumer_cls.return_value
        consumer.partitions_for_topic.return_value = None
        consumer.topics.return_value = {"system-alerts", "noc-alerts", "_consumer_offsets"}

        result = consume_topic(topic="system-alerts")

        assert result["success"] is False
        assert result["suggestions"]

    def test_multi_partition_seek_divides_max(self, mock_consumer_cls):
        consumer = mock_consumer_cls.return_value
        consumer.partitions_for_topic.return_value = {0, 1, 2, 3}
        consumer.end_offsets.side_effect = lambda tps: {tp: 200 for tp in tps}
        consumer.poll.return_value = {}

        result = consume_topic(topic="system-alerts", max_messages=20)

        assert result["success"] is True
        assert consumer.seek.call_count == 4
        seek_offsets = [call.args[1] for call in consumer.seek.call_args_list]
        for offset in seek_offsets:
            assert offset == 200 - (20 // 4)

    def test_connection_error(self, mock_consumer_cls):
        mock_consumer_cls.side_effect = NoBrokersAvailable()

        result = consume_topic(topic="system-alerts")

        assert result["success"] is False
        assert result["error"] == "connection_error"


@patch("mcp_kafka.tools.KafkaProducer")
class TestProduceMessage:
    def test_success(self, mock_producer_cls):
        producer = mock_producer_cls.return_value
        future = MagicMock()
        metadata = MagicMock()
        metadata.topic = "remediation-jobs"
        metadata.partition = 0
        metadata.offset = 42
        future.get.return_value = metadata
        producer.send.return_value = future

        result = produce_message(
            topic="remediation-jobs",
            message={"action": "restart"},
        )

        assert result["success"] is True
        assert result["offset"] == 42
        producer.close.assert_called_once()

    def test_topic_not_allowed(self, mock_producer_cls):
        result = produce_message(
            topic="system-alerts",
            message={"data": "test"},
        )

        assert result["success"] is False
        assert result["error"] == "validation_error"
        assert "not allowed for produce" in result["message"]

    def test_connection_error(self, mock_producer_cls):
        mock_producer_cls.side_effect = NoBrokersAvailable()

        result = produce_message(
            topic="remediation-jobs",
            message={"action": "restart"},
        )

        assert result["success"] is False
        assert result["error"] == "connection_error"


@patch("mcp_kafka.tools.KafkaAdminClient")
@patch("mcp_kafka.tools.KafkaConsumer")
class TestGetConsumerLag:
    def test_success(self, mock_consumer_cls, mock_admin_cls):
        consumer = mock_consumer_cls.return_value
        tp = TopicPartition("nginx-logs", 0)
        consumer.partitions_for_topic.return_value = {0}
        consumer.end_offsets.return_value = {tp: 100}

        admin = mock_admin_cls.return_value
        oam = MagicMock()
        oam.offset = 90
        admin.list_consumer_group_offsets.return_value = {tp: oam}

        result = get_consumer_lag()

        assert result["success"] is True
        assert result["total_lag"] == 10
        assert result["status"] == "healthy"
        consumer.close.assert_called_once()
        admin.close.assert_called_once()

    def test_behind_status(self, mock_consumer_cls, mock_admin_cls):
        consumer = mock_consumer_cls.return_value
        tp = TopicPartition("nginx-logs", 0)
        consumer.partitions_for_topic.return_value = {0}
        consumer.end_offsets.return_value = {tp: 1000}

        admin = mock_admin_cls.return_value
        oam = MagicMock()
        oam.offset = 0
        admin.list_consumer_group_offsets.return_value = {tp: oam}

        result = get_consumer_lag()

        assert result["success"] is True
        assert result["status"] == "behind"

    def test_connection_error(self, mock_consumer_cls, mock_admin_cls):
        mock_consumer_cls.side_effect = NoBrokersAvailable()

        result = get_consumer_lag()

        assert result["success"] is False
        assert result["error"] == "connection_error"
