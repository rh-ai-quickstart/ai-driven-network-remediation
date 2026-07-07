"""Background Kafka consumer for alert topics."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kafka import KafkaConsumer
from loguru import logger

from agent_service.kafka.alerts import parse_kafka_message

AlertHandler = Callable[["AlertMessage"], None]


@dataclass(frozen=True, slots=True)
class AlertMessage:
    topic: str
    partition: int
    offset: int
    raw_event: str


class AlertConsumer:
    """Subscribe to alert topics and dispatch parsed payloads to a handler."""

    def __init__(
        self,
        handler: AlertHandler,
        *,
        bootstrap_servers: str,
        topics: list[str],
        group_id: str,
        poll_timeout_ms: int = 1000,
    ) -> None:
        if not topics:
            raise ValueError("At least one Kafka consume topic is required")
        self._handler = handler
        self._bootstrap_servers = bootstrap_servers
        self._topics = topics
        self._group_id = group_id
        self._poll_timeout_ms = poll_timeout_ms
        self._consumer: KafkaConsumer | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="alert-consumer", daemon=True)
        self._thread.start()
        logger.info(
            "Kafka alert consumer started topics={} group_id={}",
            self._topics,
            self._group_id,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._poll_timeout_ms / 1000 + 5)
            if self._thread.is_alive():
                logger.warning("Kafka alert consumer thread still running after join timeout")
            self._thread = None
        logger.info("Kafka alert consumer stopped")

    def close(self) -> None:
        if self._consumer is not None:
            self._consumer.close()
            self._consumer = None

    def _run(self) -> None:
        from agent_service.config import GRAPH_INVOKE_TIMEOUT_SECONDS

        self._consumer = KafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            auto_offset_reset="latest",
            enable_auto_commit=True,
            # One record per poll: handler blocks for up to GRAPH_INVOKE_TIMEOUT_SECONDS.
            max_poll_records=1,
            max_poll_interval_ms=int(GRAPH_INVOKE_TIMEOUT_SECONDS * 1000 * 2),
        )
        try:
            while self._running:
                records = self._consumer.poll(timeout_ms=self._poll_timeout_ms)
                if not records:
                    continue
                for messages in records.values():
                    for msg in messages:
                        if not self._running:
                            return
                        try:
                            self._handle_message(msg)
                        except Exception:
                            logger.exception(
                                "Failed to handle Kafka message topic={} offset={}",
                                msg.topic,
                                msg.offset,
                            )
        finally:
            self.close()

    def _handle_message(self, msg: Any) -> None:
        raw_event = parse_kafka_message(msg.topic, msg.value)
        if raw_event is None:
            return
        alert = AlertMessage(
            topic=msg.topic,
            partition=msg.partition,
            offset=msg.offset,
            raw_event=raw_event,
        )
        logger.info(
            "Kafka alert received topic={} partition={} offset={}",
            alert.topic,
            alert.partition,
            alert.offset,
        )
        self._handler(alert)
