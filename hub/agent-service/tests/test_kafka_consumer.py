from unittest.mock import MagicMock, patch

import pytest

from agent_service.kafka.consumer import AlertConsumer, AlertMessage


def _make_message(*, topic="system-alerts", partition=0, offset=42, value=b'{"message":"OOMKilled"}'):
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = value
    return msg


class TestAlertConsumerInit:
    def test_requires_at_least_one_topic(self):
        with pytest.raises(ValueError, match="At least one Kafka consume topic"):
            AlertConsumer(
                lambda _alert: None,
                bootstrap_servers="kafka:9092",
                topics=[],
                group_id="test-group",
            )


class TestHandleMessage:
    def test_dispatches_parsed_alert(self):
        handled: list[AlertMessage] = []

        consumer = AlertConsumer(
            handled.append,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts"],
            group_id="test-group",
        )
        consumer._handle_message(_make_message())

        assert len(handled) == 1
        assert handled[0].topic == "system-alerts"
        assert handled[0].partition == 0
        assert handled[0].offset == 42
        assert "OOMKilled" in handled[0].raw_event

    def test_skips_unsupported_topic(self):
        handled: list[AlertMessage] = []

        consumer = AlertConsumer(
            handled.append,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts"],
            group_id="test-group",
        )
        consumer._handle_message(_make_message(topic="nginx-logs"))

        assert handled == []

    def test_skips_empty_payload(self):
        handled: list[AlertMessage] = []

        consumer = AlertConsumer(
            handled.append,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts"],
            group_id="test-group",
        )
        consumer._handle_message(_make_message(value=b"   "))

        assert handled == []


class TestConsumerLifecycle:
    @patch("agent_service.kafka.consumer.KafkaConsumer")
    def test_start_and_stop(self, mock_kafka_consumer_cls):
        mock_consumer = MagicMock()
        mock_consumer.poll.return_value = {}
        mock_kafka_consumer_cls.return_value = mock_consumer

        consumer = AlertConsumer(
            lambda _alert: None,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts", "noc-alerts"],
            group_id="dark-noc-agent",
            poll_timeout_ms=100,
        )

        consumer.start()
        consumer.stop()

        mock_kafka_consumer_cls.assert_called_once()
        _, kwargs = mock_kafka_consumer_cls.call_args
        assert kwargs["bootstrap_servers"] == "kafka:9092"
        assert kwargs["group_id"] == "dark-noc-agent"
        assert kwargs["auto_offset_reset"] == "latest"
        assert kwargs["max_poll_records"] == 1
        assert kwargs["max_poll_interval_ms"] == 600_000
        mock_consumer.close.assert_called()

    @patch("agent_service.kafka.consumer.KafkaConsumer")
    def test_run_invokes_handler_for_each_message(self, mock_kafka_consumer_cls):
        handled: list[AlertMessage] = []
        messages = [
            _make_message(offset=1, value=b"alert one"),
            _make_message(offset=2, value=b"alert two"),
        ]
        mock_consumer = MagicMock()
        mock_kafka_consumer_cls.return_value = mock_consumer

        consumer = AlertConsumer(
            handled.append,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts"],
            group_id="test-group",
            poll_timeout_ms=100,
        )
        consumer._running = True

        def poll_fn(timeout_ms=1000):
            if poll_fn.seen:
                consumer._running = False
                return {}
            poll_fn.seen = True
            return {(0, 0): messages}

        poll_fn.seen = False
        mock_consumer.poll.side_effect = poll_fn
        consumer._run()

        assert len(handled) == 2
        assert handled[0].offset == 1
        assert handled[1].offset == 2

    @patch("agent_service.kafka.consumer.KafkaConsumer")
    def test_handler_exception_does_not_stop_consumer(self, mock_kafka_consumer_cls):
        handled: list[AlertMessage] = []
        messages = [
            _make_message(offset=1, value=b"alert one"),
            _make_message(offset=2, value=b"alert two"),
        ]
        mock_consumer = MagicMock()
        mock_kafka_consumer_cls.return_value = mock_consumer

        def handler(alert: AlertMessage) -> None:
            if alert.offset == 1:
                raise RuntimeError("handler failed")
            handled.append(alert)

        consumer = AlertConsumer(
            handler,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts"],
            group_id="test-group",
            poll_timeout_ms=100,
        )
        consumer._running = True

        def poll_fn(timeout_ms=1000):
            if poll_fn.seen:
                consumer._running = False
                return {}
            poll_fn.seen = True
            return {(0, 0): messages}

        poll_fn.seen = False
        mock_consumer.poll.side_effect = poll_fn
        consumer._run()

        assert len(handled) == 1
        assert handled[0].offset == 2

    @patch("agent_service.kafka.consumer.logger.warning")
    def test_stop_warns_when_thread_still_alive(self, mock_warning):
        consumer = AlertConsumer(
            lambda _alert: None,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts"],
            group_id="test-group",
            poll_timeout_ms=100,
        )
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        consumer._thread = mock_thread

        consumer.stop()

        mock_thread.join.assert_called_once()
        mock_warning.assert_called_once_with(
            "Kafka alert consumer thread still running after join timeout",
        )

    @patch("agent_service.kafka.consumer.KafkaConsumer")
    def test_stop_closes_consumer_once(self, mock_kafka_consumer_cls):
        mock_consumer = MagicMock()
        mock_consumer.poll.return_value = {}
        mock_kafka_consumer_cls.return_value = mock_consumer

        consumer = AlertConsumer(
            lambda _alert: None,
            bootstrap_servers="kafka:9092",
            topics=["system-alerts"],
            group_id="test-group",
            poll_timeout_ms=100,
        )

        consumer.start()
        consumer.stop()

        assert mock_consumer.close.call_count == 1
