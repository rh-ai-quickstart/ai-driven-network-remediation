"""Tests for the RAN anomaly Kafka consumer."""

from __future__ import annotations

import threading
import time

import pytest

import ran_rca_service.kafka.consumer as consumer_module
from ran_rca_service.kafka.consumer import AnomalyConsumer


class _FakeMessage:
    def __init__(self, topic: str, partition: int, offset: int, value: bytes) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.value = value


class _FakeKafkaConsumer:
    def __init__(self, *args, **kwargs) -> None:
        self.closed = False
        self._delivered = False

    def poll(self, timeout_ms: int = 1000):
        if self._delivered or self.closed:
            return {}
        self._delivered = True
        return {
            ("ran-anomalies", 0): [
                _FakeMessage("ran-anomalies", 0, 0, b'{"cell_id":1}'),
                _FakeMessage("ran-anomalies", 0, 1, b'{"cell_id":2}'),
            ]
        }

    def close(self) -> None:
        self.closed = True


class _FlakyThenHealthyKafkaConsumer:
    instances: list[_FlakyThenHealthyKafkaConsumer] = []

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
            ("ran-anomalies", 0): [
                _FakeMessage("ran-anomalies", 0, 0, b'{"cell_id":1}'),
                _FakeMessage("ran-anomalies", 0, 1, b'{"cell_id":2}'),
            ]
        }

    def close(self) -> None:
        self.closed = True


def test_topic_is_required():
    with pytest.raises(ValueError):
        AnomalyConsumer(lambda value: None, bootstrap_servers="kafka:9092", topic="", group_id="g")


def test_start_dispatches_polled_messages_to_handler(monkeypatch):
    monkeypatch.setattr(consumer_module, "KafkaConsumer", _FakeKafkaConsumer)

    received: list[bytes] = []
    lock = threading.Lock()

    def handler(value: bytes) -> None:
        with lock:
            received.append(value)

    consumer = AnomalyConsumer(
        handler,
        bootstrap_servers="kafka:9092",
        topic="ran-anomalies",
        group_id="test-group",
        poll_timeout_ms=50,
    )
    consumer.start()

    deadline = time.time() + 2
    while time.time() < deadline and len(received) < 2:
        time.sleep(0.05)

    assert consumer.is_connected
    consumer.stop()

    assert received == [b'{"cell_id":1}', b'{"cell_id":2}']


def test_handler_exception_does_not_crash_consumer_loop(monkeypatch):
    monkeypatch.setattr(consumer_module, "KafkaConsumer", _FakeKafkaConsumer)

    call_count = {"n": 0}

    def failing_handler(value: bytes) -> None:
        call_count["n"] += 1
        raise RuntimeError("boom")

    consumer = AnomalyConsumer(
        failing_handler,
        bootstrap_servers="kafka:9092",
        topic="ran-anomalies",
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
    consumer = AnomalyConsumer(
        lambda value: None,
        bootstrap_servers="kafka:9092",
        topic="ran-anomalies",
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


def test_poll_failure_triggers_reconnect(monkeypatch):
    monkeypatch.setattr(consumer_module, "KafkaConsumer", _FlakyThenHealthyKafkaConsumer)
    _FlakyThenHealthyKafkaConsumer.instances = []

    received: list[bytes] = []
    lock = threading.Lock()

    def handler(value: bytes) -> None:
        with lock:
            received.append(value)

    consumer = AnomalyConsumer(
        handler,
        bootstrap_servers="kafka:9092",
        topic="ran-anomalies",
        group_id="test-group",
        poll_timeout_ms=50,
    )
    monkeypatch.setattr(consumer._stop_event, "wait", lambda timeout: None)

    consumer.start()

    deadline = time.time() + 2
    while time.time() < deadline and len(received) < 2:
        time.sleep(0.05)

    consumer.stop()

    assert received == [b'{"cell_id":1}', b'{"cell_id":2}']
    assert len(_FlakyThenHealthyKafkaConsumer.instances) == 2
    assert _FlakyThenHealthyKafkaConsumer.instances[0].closed is True


def test_is_connected_false_before_start():
    consumer = AnomalyConsumer(
        lambda value: None,
        bootstrap_servers="kafka:9092",
        topic="ran-anomalies",
        group_id="test-group",
    )

    assert consumer.is_connected is False
