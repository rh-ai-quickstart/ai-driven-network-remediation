"""Unit tests for AnomaliesConsumer: the background thread that fills the
in-memory anomalies buffer, replacing the old per-request KafkaConsumer."""

import json
import threading
import time
from collections import deque

import pytest
import ran_chatbot_service.kafka as kafka_module
from ran_chatbot_service.kafka import AnomaliesConsumer
from ran_chatbot_service.models import EnrichedAnomaly

_SAMPLE_ANOMALY_1 = {
    "incident_id": "inc-001",
    "zone": "A",
    "application": "Twitch",
    "kpi_window": [{"RSRP": -85.0}] * 128,
    "ad_label": "anomalous",
    "ad_confidence": 0.94,
    "root_cause": "Signal degradation in zone A.",
    "recommended_fix": "Section 4.2 — Verify antenna alignment.",
}
_SAMPLE_ANOMALY_2 = {**_SAMPLE_ANOMALY_1, "incident_id": "inc-002"}
_TOPIC = "ran-anomalies-enriched"


class _FakeMessage:
    def __init__(self, offset: int, value: str) -> None:
        self.topic = _TOPIC
        self.partition = 0
        self.offset = offset
        self.value = value


class _FakeKafkaConsumer:
    """Minimal stand-in for kafka.KafkaConsumer supporting AnomaliesConsumer's
    connect -> seed (poll for assignment, seek, drain) -> poll loop sequence.

    poll() call #1 is always the seed's assignment-discovery call and #2 is
    always the seed's drain call; `deliver_on_call` controls which call number
    (of any of them) actually returns the fixed message batch, so the same
    fake can exercise either "seed pre-populates the buffer" (deliver_on_call=2)
    or "the regular poll loop delivers it" (deliver_on_call=3) scenarios.
    """

    def __init__(self, *args, deliver_on_call: int = 3, messages=None, **kwargs) -> None:
        self.closed = False
        self._poll_count = 0
        self._deliver_on_call = deliver_on_call
        self._delivered = False
        self._messages = (
            messages
            if messages is not None
            else [
                _FakeMessage(0, json.dumps(_SAMPLE_ANOMALY_1)),
                _FakeMessage(1, json.dumps(_SAMPLE_ANOMALY_2)),
            ]
        )

    def poll(self, timeout_ms: int = 1000):
        self._poll_count += 1
        if self.closed or self._delivered or self._poll_count != self._deliver_on_call:
            return {}
        self._delivered = True
        return {(_TOPIC, 0): self._messages}

    def assignment(self):
        return {(_TOPIC, 0)}

    def end_offsets(self, partitions):
        return {p: 0 for p in partitions}

    def seek(self, partition, offset) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _NoPartitionsKafkaConsumer(_FakeKafkaConsumer):
    """No partitions ever assigned, so no messages are ever available either."""

    def assignment(self):
        return set()

    def poll(self, timeout_ms: int = 1000):
        return {}


class _FlakyThenHealthyKafkaConsumer:
    """Connects successfully every time, but the first instance's poll() raises
    once (simulating a broker drop mid-stream) before the consumer reconnects
    and a second, healthy instance delivers the batch."""

    instances: list["_FlakyThenHealthyKafkaConsumer"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.closed = False
        self._is_first = len(_FlakyThenHealthyKafkaConsumer.instances) == 0
        self._healthy = _FakeKafkaConsumer(deliver_on_call=2) if not self._is_first else None
        _FlakyThenHealthyKafkaConsumer.instances.append(self)

    def poll(self, timeout_ms: int = 1000):
        if self._is_first:
            raise RuntimeError("broker connection dropped")
        return self._healthy.poll(timeout_ms)

    def assignment(self):
        return self._healthy.assignment()

    def end_offsets(self, partitions):
        return self._healthy.end_offsets(partitions)

    def seek(self, partition, offset) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        time.sleep(0.05)


def test_topic_is_required():
    with pytest.raises(ValueError):
        AnomaliesConsumer(deque(), bootstrap_servers="kafka:9092", topic="", max_messages=50)


def test_is_connected_false_before_start():
    consumer = AnomaliesConsumer(deque(), bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50)
    assert consumer.is_connected is False


def test_seed_recent_history_prepopulates_buffer_on_connect(monkeypatch):
    """The seek-back-and-drain step must land messages in the buffer right after
    connecting, without waiting for the regular poll loop to deliver anything."""
    monkeypatch.setattr(kafka_module, "KafkaConsumer", lambda *a, **kw: _FakeKafkaConsumer(deliver_on_call=2))

    buffer: deque[EnrichedAnomaly] = deque(maxlen=50)
    consumer = AnomaliesConsumer(
        buffer, bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50, poll_timeout_ms=50
    )
    consumer.start()

    _wait_until(lambda: len(buffer) >= 2)
    consumer.stop()

    assert [a.incident_id for a in buffer] == ["inc-001", "inc-002"]


def test_poll_loop_dispatches_new_messages_to_buffer(monkeypatch):
    monkeypatch.setattr(kafka_module, "KafkaConsumer", lambda *a, **kw: _FakeKafkaConsumer(deliver_on_call=3))

    buffer: deque[EnrichedAnomaly] = deque(maxlen=50)
    consumer = AnomaliesConsumer(
        buffer, bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50, poll_timeout_ms=50
    )
    consumer.start()

    _wait_until(lambda: len(buffer) >= 2)
    assert consumer.is_connected
    consumer.stop()

    assert [a.incident_id for a in buffer] == ["inc-001", "inc-002"]


def test_buffer_respects_maxlen():
    """deque(maxlen=N) evicting the oldest entries is what bounds memory usage —
    sanity check this holds for EnrichedAnomaly instances the way the rest of the
    consumer relies on it."""
    buffer: deque[EnrichedAnomaly] = deque(maxlen=1)
    buffer.append(EnrichedAnomaly(**_SAMPLE_ANOMALY_1))
    buffer.append(EnrichedAnomaly(**_SAMPLE_ANOMALY_2))
    assert [a.incident_id for a in buffer] == ["inc-002"]


def test_skips_malformed_message_without_crashing_the_loop(monkeypatch):
    """Regression test: a single malformed/schema-invalid message in a batch must
    not crash the poll loop or prevent the rest of the batch (and later batches)
    from being processed."""
    bad_then_good = [
        _FakeMessage(0, "not valid json"),
        _FakeMessage(1, json.dumps(_SAMPLE_ANOMALY_1)),
    ]
    monkeypatch.setattr(
        kafka_module,
        "KafkaConsumer",
        lambda *a, **kw: _FakeKafkaConsumer(deliver_on_call=2, messages=bad_then_good),
    )

    buffer: deque[EnrichedAnomaly] = deque(maxlen=50)
    consumer = AnomaliesConsumer(
        buffer, bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50, poll_timeout_ms=50
    )
    consumer.start()

    _wait_until(lambda: len(buffer) >= 1)
    consumer.stop()

    assert [a.incident_id for a in buffer] == ["inc-001"]


def test_returns_empty_buffer_when_no_partitions_assigned(monkeypatch):
    monkeypatch.setattr(kafka_module, "KafkaConsumer", lambda *a, **kw: _NoPartitionsKafkaConsumer())

    buffer: deque[EnrichedAnomaly] = deque(maxlen=50)
    consumer = AnomaliesConsumer(
        buffer, bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50, poll_timeout_ms=50
    )
    consumer.start()

    time.sleep(0.2)
    assert consumer.is_connected
    consumer.stop()

    assert list(buffer) == []


def test_consumer_closed_when_stopped_immediately_after_connecting(monkeypatch):
    """Regression test: if _running flips to False in the exact window right after
    KafkaConsumer() succeeds but before the poll loop starts, the already-created
    consumer must still be closed (not leaked)."""
    consumer = AnomaliesConsumer(deque(), bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50)

    created: list[_FakeKafkaConsumer] = []

    class _RaceKafkaConsumer(_FakeKafkaConsumer):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)
            consumer._running = False  # simulate stop() firing right after connect succeeds

    monkeypatch.setattr(kafka_module, "KafkaConsumer", _RaceKafkaConsumer)

    consumer._running = True
    consumer._run()

    assert len(created) == 1
    assert created[0].closed is True
    assert consumer._consumer is None


def test_poll_failure_triggers_reconnect_instead_of_killing_the_thread(monkeypatch):
    """Regression test: if poll() raises after a successful connect (broker restart,
    auth failure, deleted topic, ...), the consumer must reconnect and keep filling
    the buffer rather than letting the thread die silently."""
    monkeypatch.setattr(kafka_module, "KafkaConsumer", _FlakyThenHealthyKafkaConsumer)
    _FlakyThenHealthyKafkaConsumer.instances = []

    buffer: deque[EnrichedAnomaly] = deque(maxlen=50)
    consumer = AnomaliesConsumer(
        buffer, bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50, poll_timeout_ms=50
    )
    # Avoid waiting out the real 5s backoff between the failed and healthy connections.
    monkeypatch.setattr(consumer._stop_event, "wait", lambda timeout: None)

    consumer.start()

    _wait_until(lambda: len(buffer) >= 2)
    consumer.stop()

    assert [a.incident_id for a in buffer] == ["inc-001", "inc-002"]
    assert len(_FlakyThenHealthyKafkaConsumer.instances) == 2
    assert _FlakyThenHealthyKafkaConsumer.instances[0].closed is True


def test_thread_safe_reads_while_appending(monkeypatch):
    """Sanity check that reading the buffer (list(buffer), as /api/chat does)
    concurrently with the background thread appending doesn't raise — deque
    append/iteration is safe under the GIL, same pattern ran-anomaly-detector's
    /anomalies endpoint already relies on."""
    monkeypatch.setattr(kafka_module, "KafkaConsumer", lambda *a, **kw: _FakeKafkaConsumer(deliver_on_call=2))

    buffer: deque[EnrichedAnomaly] = deque(maxlen=50)
    consumer = AnomaliesConsumer(
        buffer, bootstrap_servers="kafka:9092", topic=_TOPIC, max_messages=50, poll_timeout_ms=10
    )
    consumer.start()

    errors: list[Exception] = []

    def _reader() -> None:
        for _ in range(50):
            try:
                list(buffer)
            except Exception as exc:  # pragma: no cover - would fail the test either way
                errors.append(exc)

    reader_thread = threading.Thread(target=_reader)
    reader_thread.start()
    reader_thread.join(timeout=2)
    consumer.stop()

    assert errors == []
