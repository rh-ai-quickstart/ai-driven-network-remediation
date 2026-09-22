"""Background source of enriched RAN anomalies for the chatbot to talk about.

A single background thread (AnomaliesConsumer) owns the Kafka connection and
continuously fills an in-memory buffer with enriched anomalies (root_cause +
recommended_fix added) that ran-rca-service publishes to the
ran-anomalies-enriched Kafka topic. Request handlers just read the buffer —
no per-request Kafka I/O, no thread-safety concerns (deque append/read is
safe under the GIL), matching the pattern already used by
hub/ran-anomaly-detector/src/ran_anomaly_detector/kafka/consumer.py.

Deliberately does NOT use a Kafka consumer group_id: ran-anomalies-enriched
has multiple partitions, and a shared group would let Kafka split those
partitions across ran-chatbot-service replicas (if ever scaled beyond one),
giving each replica only a partial view. Staying group-less means every
replica independently sees the full topic, exactly like the previous
per-request implementation did.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

from kafka import KafkaConsumer
from pydantic import ValidationError

import json

from .models import EnrichedAnomaly

logger = logging.getLogger(__name__)


class AnomaliesConsumer:
    """Continuously consume ran-anomalies-enriched into a bounded in-memory buffer.

    The buffer is in ascending Kafka offset order (oldest first): messages are
    always appended in the order they're dispatched, so the newest anomaly is
    buffer[-1], not buffer[0] — see chat.py's build_chat_context()/
    format_chat_reply(), which index from the tail for exactly this reason.
    """

    def __init__(
        self,
        buffer: deque[EnrichedAnomaly],
        *,
        bootstrap_servers: str,
        topic: str,
        max_messages: int,
        poll_timeout_ms: int = 1000,
    ) -> None:
        if not topic:
            raise ValueError("An enriched-anomalies Kafka topic is required")
        self._buffer = buffer
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._max_messages = max_messages
        self._poll_timeout_ms = poll_timeout_ms
        self._consumer: KafkaConsumer | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ran-anomalies-consumer", daemon=True)
        self._thread.start()
        logger.info("Kafka enriched-anomalies consumer started topic=%s", self._topic)

    @property
    def is_connected(self) -> bool:
        return self._running and self._consumer is not None

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_timeout_ms / 1000 + 5)
            if self._thread.is_alive():
                logger.warning("Kafka enriched-anomalies consumer thread still running after join timeout")
            self._thread = None
        logger.info("Kafka enriched-anomalies consumer stopped")

    def close(self) -> None:
        if self._consumer is not None:
            self._consumer.close()
            self._consumer = None

    def _run(self) -> None:
        """Connect, seed recent history, poll, and transparently reconnect if the
        poll loop ever fails.

        A dead broker connection, expired auth, or a deleted topic can make
        `poll()` raise *after* a successful connect. Without catching that here,
        the thread would exit for good and the buffer would silently go stale
        (while /ready still reports whatever it last saw) until restarted.
        """
        while self._running:
            if not self._connect():
                return
            try:
                self._seed_recent_history()
                self._poll_loop()
            except Exception:
                logger.exception(
                    "Kafka enriched-anomalies poll loop failed, reconnecting to %s in 5s",
                    self._bootstrap_servers,
                )
                self._stop_event.wait(5)
            finally:
                self.close()

    def _connect(self) -> bool:
        """Retry connecting to Kafka every 5s until successful or stop() is called.

        No group_id — see module docstring for why.
        """
        while self._running:
            try:
                self._consumer = KafkaConsumer(
                    self._topic,
                    bootstrap_servers=self._bootstrap_servers,
                    auto_offset_reset="latest",
                    enable_auto_commit=False,
                    value_deserializer=lambda m: m.decode("utf-8", errors="replace"),
                )
                return True
            except Exception:
                logger.warning("Kafka not reachable at %s, retrying in 5s", self._bootstrap_servers)
                self._stop_event.wait(5)
        return False

    def _seed_recent_history(self) -> None:
        """Seek each assigned partition back a bounded window and drain it, so the
        buffer is populated with recent history immediately after (re)connecting
        instead of only filling in as new anomalies trickle in over time."""
        self._consumer.poll(timeout_ms=800)
        partitions = self._consumer.assignment()
        if not partitions:
            logger.debug("No partitions assigned for topic %s", self._topic)
            return

        max_per_partition = max(10, self._max_messages // max(1, len(partitions)))
        for tp in partitions:
            end_offset = self._consumer.end_offsets([tp])[tp]
            start_offset = max(0, end_offset - max_per_partition)
            self._consumer.seek(tp, start_offset)

        records = self._consumer.poll(timeout_ms=1000)
        if records:
            self._dispatch(records)

    def _poll_loop(self) -> None:
        while self._running:
            records = self._consumer.poll(timeout_ms=self._poll_timeout_ms)
            if not records:
                continue
            self._dispatch(records)

    def _dispatch(self, records: Any) -> None:
        """Parse each polled message into EnrichedAnomaly and append to the buffer.

        model_validate_json wraps both malformed JSON and schema mismatches
        (missing/misnamed/mistyped fields) in the same ValidationError, so a
        renamed field upstream is caught here instead of surfacing as a silent
        None deep in the chat reply.
        """
        for messages in records.values():
            for msg in messages:
                if not self._running:
                    return
                try:
                    self._buffer.append(EnrichedAnomaly.model_validate_json(msg.value))
                except ValidationError:
                    logger.warning(
                        "Skipping enriched anomaly that failed schema validation at offset %s",
                        msg.offset,
                    )


class RemediationConsumer:
    """Continuously consume ran-remediation-results into a bounded in-memory buffer.

    Mirrors AnomaliesConsumer in structure. No consumer group_id for the same
    reason: every replica of ran-chatbot-service needs the full view of
    remediation results to correctly join against its anomaly buffer.

    Records are stored as plain dicts — just the fields needed for the join:
    incident_id, success, job_id, timestamp.
    """

    def __init__(
        self,
        buffer: deque[dict],
        *,
        bootstrap_servers: str,
        topic: str,
        max_messages: int,
        poll_timeout_ms: int = 1000,
    ) -> None:
        if not topic:
            raise ValueError("A remediation-results Kafka topic is required")
        self._buffer = buffer
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._max_messages = max_messages
        self._poll_timeout_ms = poll_timeout_ms
        self._consumer: KafkaConsumer | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ran-remediation-consumer", daemon=True)
        self._thread.start()
        logger.info("Kafka remediation-results consumer started topic=%s", self._topic)

    @property
    def is_connected(self) -> bool:
        return self._running and self._consumer is not None

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_timeout_ms / 1000 + 5)
            if self._thread.is_alive():
                logger.warning("Kafka remediation-results consumer thread still running after join timeout")
            self._thread = None
        logger.info("Kafka remediation-results consumer stopped")

    def close(self) -> None:
        if self._consumer is not None:
            self._consumer.close()
            self._consumer = None

    def _run(self) -> None:
        while self._running:
            if not self._connect():
                return
            try:
                self._seed_recent_history()
                self._poll_loop()
            except Exception:
                logger.exception(
                    "Kafka remediation-results poll loop failed, reconnecting to %s in 5s",
                    self._bootstrap_servers,
                )
                self._stop_event.wait(5)
            finally:
                self.close()

    def _connect(self) -> bool:
        while self._running:
            try:
                self._consumer = KafkaConsumer(
                    self._topic,
                    bootstrap_servers=self._bootstrap_servers,
                    auto_offset_reset="latest",
                    enable_auto_commit=False,
                    value_deserializer=lambda m: m.decode("utf-8", errors="replace"),
                )
                return True
            except Exception:
                logger.warning("Kafka not reachable at %s, retrying in 5s", self._bootstrap_servers)
                self._stop_event.wait(5)
        return False

    def _seed_recent_history(self) -> None:
        self._consumer.poll(timeout_ms=800)
        partitions = self._consumer.assignment()
        if not partitions:
            return
        max_per_partition = max(10, self._max_messages // max(1, len(partitions)))
        for tp in partitions:
            end_offset = self._consumer.end_offsets([tp])[tp]
            start_offset = max(0, end_offset - max_per_partition)
            self._consumer.seek(tp, start_offset)
        records = self._consumer.poll(timeout_ms=1000)
        if records:
            self._dispatch(records)

    def _poll_loop(self) -> None:
        while self._running:
            records = self._consumer.poll(timeout_ms=self._poll_timeout_ms)
            if not records:
                continue
            self._dispatch(records)

    def _dispatch(self, records: Any) -> None:
        for messages in records.values():
            for msg in messages:
                if not self._running:
                    return
                try:
                    record = json.loads(msg.value)
                    if "incident_id" not in record:
                        raise ValueError("missing incident_id")
                    self._buffer.append(record)
                except Exception:
                    logger.warning(
                        "Skipping remediation result that failed to parse at offset %s",
                        msg.offset,
                    )
