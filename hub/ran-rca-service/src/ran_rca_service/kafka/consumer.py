"""Background Kafka consumer for the ran-anomalies topic."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from kafka import KafkaConsumer
from loguru import logger

AnomalyHandler = Callable[[bytes], None]


class AnomalyConsumer:
    """Subscribe to the RAN anomalies topic and dispatch raw message values to a handler."""

    def __init__(
        self,
        handler: AnomalyHandler,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        poll_timeout_ms: int = 1000,
    ) -> None:
        if not topic:
            raise ValueError("A Kafka anomalies topic is required")
        self._handler = handler
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._poll_timeout_ms = poll_timeout_ms
        self._consumer: KafkaConsumer | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ran-anomaly-consumer", daemon=True)
        self._thread.start()
        logger.info(
            "Kafka RAN anomaly consumer started topic={} group_id={}",
            self._topic,
            self._group_id,
        )

    @property
    def is_connected(self) -> bool:
        return self._running and self._consumer is not None

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_timeout_ms / 1000 + 5)
            if self._thread.is_alive():
                logger.warning("Kafka RAN anomaly consumer thread still running after join timeout")
            self._thread = None
        logger.info("Kafka RAN anomaly consumer stopped")

    def close(self) -> None:
        if self._consumer is not None:
            self._consumer.close()
            self._consumer = None

    def _run(self) -> None:
        while self._running:
            if not self._connect():
                return
            try:
                self._poll_loop()
            except Exception:
                logger.exception(
                    "Kafka RAN anomaly poll loop failed, reconnecting to {} in 5s",
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
                    group_id=self._group_id,
                    auto_offset_reset="latest",
                    enable_auto_commit=True,
                    max_poll_records=10,
                )
                return True
            except Exception:
                logger.warning("Kafka not reachable at {}, retrying in 5s", self._bootstrap_servers)
                self._stop_event.wait(5)
        return False

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
                    self._handle_message(msg)
                except Exception:
                    logger.exception(
                        "Failed to handle RAN anomaly message topic={} offset={}",
                        msg.topic,
                        msg.offset,
                    )

    def _handle_message(self, msg: Any) -> None:
        logger.info(
            "RAN anomaly message received topic={} partition={} offset={}",
            msg.topic,
            msg.partition,
            msg.offset,
        )
        self._handler(msg.value)
