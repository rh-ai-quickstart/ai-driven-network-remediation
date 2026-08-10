import threading
import time

import pytest
import shared.kafka.consumer as consumer_module
from shared.kafka import TopicConsumer


class _FakeMessage:
    def __init__(self, topic: str, partition: int, offset: int, value: bytes) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.value = value


class _FakeKafkaConsumer:
    """Minimal stand-in for kafka.KafkaConsumer, delivering a fixed batch once then idling."""

    def __init__(self, *args, **kwargs) -> None:
        self.closed = False
        self._delivered = False

    def poll(self, timeout_ms: int = 1000):
        if self._delivered or self.closed:
            return {}
        self._delivered = True
        return {
            ("test-topic", 0): [
                _FakeMessage("test-topic", 0, 0, b"first"),
                _FakeMessage("test-topic", 0, 1, b"second"),
            ]
        }

    def close(self) -> None:
        self.closed = True


class _FlakyThenHealthyKafkaConsumer:
    """Connects successfully every time, but the first instance's poll() raises once
    (simulating a broker drop mid-stream) before the consumer reconnects and a second,
    healthy instance delivers the batch."""

    instances: list["_FlakyThenHealthyKafkaConsumer"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.closed = False
        self._delivered = False
        self._is_first = len(_FlakyThenHealthyKafkaConsumer.instances) == 0
        _FlakyThenHealthyKafkaConsumer.instances.append(self)

    def poll(self, timeout_ms: int = 1000):
        if self._is_first:
            raise RuntimeError("broker connection dropped")
        if self._delivered or self.closed:
            return {}
        self._delivered = True
        return {
            ("test-topic", 0): [
                _FakeMessage("test-topic", 0, 0, b"first"),
                _FakeMessage("test-topic", 0, 1, b"second"),
            ]
        }

    def close(self) -> None:
        self.closed = True


def test_topic_is_required():
    with pytest.raises(ValueError):
        TopicConsumer(lambda value: None, name="test", bootstrap_servers="kafka:9092", topic="", group_id="g")


def test_start_dispatches_polled_messages_to_handler(monkeypatch):
    monkeypatch.setattr(consumer_module, "KafkaConsumer", _FakeKafkaConsumer)

    received: list[bytes] = []
    lock = threading.Lock()

    def handler(value: bytes) -> None:
        with lock:
            received.append(value)

    consumer = TopicConsumer(
        handler,
        name="test",
        bootstrap_servers="kafka:9092",
        topic="test-topic",
        group_id="test-group",
        poll_timeout_ms=50,
    )
    consumer.start()

    deadline = time.time() + 2
    while time.time() < deadline and len(received) < 2:
        time.sleep(0.05)

    assert consumer.is_connected
    consumer.stop()

    assert received == [b"first", b"second"]


def test_handler_exception_does_not_crash_consumer_loop(monkeypatch):
    monkeypatch.setattr(consumer_module, "KafkaConsumer", _FakeKafkaConsumer)

    call_count = {"n": 0}

    def failing_handler(value: bytes) -> None:
        call_count["n"] += 1
        raise RuntimeError("boom")

    consumer = TopicConsumer(
        failing_handler,
        name="test",
        bootstrap_servers="kafka:9092",
        topic="test-topic",
        group_id="test-group",
        poll_timeout_ms=50,
    )
    consumer.start()

    deadline = time.time() + 2
    while time.time() < deadline and call_count["n"] < 2:
        time.sleep(0.05)

    consumer.stop()

    assert call_count["n"] == 2


def test_consumer_closed_when_stopped_immediately_after_connecting(monkeypatch):
    """Regression test: if _running flips to False in the exact window right after
    KafkaConsumer() succeeds but before the poll loop starts, the already-created
    consumer must still be closed (not leaked)."""
    consumer = TopicConsumer(
        lambda value: None,
        name="test",
        bootstrap_servers="kafka:9092",
        topic="test-topic",
        group_id="test-group",
    )

    created: list[_FakeKafkaConsumer] = []

    class _RaceKafkaConsumer(_FakeKafkaConsumer):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)
            consumer._running = False

    monkeypatch.setattr(consumer_module, "KafkaConsumer", _RaceKafkaConsumer)

    consumer._running = True
    consumer._run()

    assert len(created) == 1
    assert created[0].closed is True
    assert consumer._consumer is None


def test_poll_failure_triggers_reconnect_instead_of_killing_the_thread(monkeypatch):
    """Regression test: if poll() raises after a successful connect, the consumer
    must reconnect and keep processing messages."""
    monkeypatch.setattr(consumer_module, "KafkaConsumer", _FlakyThenHealthyKafkaConsumer)
    _FlakyThenHealthyKafkaConsumer.instances = []

    received: list[bytes] = []
    lock = threading.Lock()

    def handler(value: bytes) -> None:
        with lock:
            received.append(value)

    consumer = TopicConsumer(
        handler,
        name="test",
        bootstrap_servers="kafka:9092",
        topic="test-topic",
        group_id="test-group",
        poll_timeout_ms=50,
    )
    monkeypatch.setattr(consumer._stop_event, "wait", lambda timeout: None)

    consumer.start()

    deadline = time.time() + 2
    while time.time() < deadline and len(received) < 2:
        time.sleep(0.05)

    consumer.stop()

    assert received == [b"first", b"second"]
    assert len(_FlakyThenHealthyKafkaConsumer.instances) == 2
    assert _FlakyThenHealthyKafkaConsumer.instances[0].closed is True


def test_is_connected_false_before_start():
    consumer = TopicConsumer(
        lambda value: None,
        name="test",
        bootstrap_servers="kafka:9092",
        topic="test-topic",
        group_id="test-group",
    )

    assert consumer.is_connected is False


def test_name_is_used_in_thread_name(monkeypatch):
    monkeypatch.setattr(consumer_module, "KafkaConsumer", _FakeKafkaConsumer)

    consumer = TopicConsumer(
        lambda value: None,
        name="ran-metrics",
        bootstrap_servers="kafka:9092",
        topic="test-topic",
        group_id="test-group",
        poll_timeout_ms=50,
    )
    consumer.start()

    assert consumer._thread is not None
    assert consumer._thread.name == "ran-metrics-consumer"

    consumer.stop()
