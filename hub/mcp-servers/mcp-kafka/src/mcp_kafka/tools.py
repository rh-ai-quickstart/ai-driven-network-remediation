"""Kafka tool implementations."""

import json
import logging
from datetime import datetime, timezone

from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
from kafka.structs import TopicPartition

from . import config
from .config import mcp
from .utils import _resolve_topic, format_error
from .validators import (
    clamp,
    suggest_topics,
)

logger = logging.getLogger(__name__)


def _seek_to_tail(consumer, topic, max_messages):
    partitions = consumer.partitions_for_topic(topic)
    if not partitions:
        return None

    tp_list = [TopicPartition(topic, p) for p in partitions]
    consumer.assign(tp_list)

    end_offsets = consumer.end_offsets(tp_list)
    seek_depth = max(1, max_messages // len(tp_list))
    for tp in tp_list:
        start_offset = max(0, end_offsets[tp] - seek_depth)
        consumer.seek(tp, start_offset)

    return tp_list


def _parse_value(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _poll_messages(consumer, max_messages, timeout_ms):
    # Use poll() with an explicit timeout instead of the iterator so that the
    # timeout budget starts HERE, not back when KafkaConsumer was constructed.
    records = consumer.poll(timeout_ms=timeout_ms, max_records=max_messages)
    messages = []
    for tp_msgs in records.values():
        for msg in tp_msgs:
            messages.append(
                {
                    "partition": msg.partition,
                    "offset": msg.offset,
                    "timestamp": datetime.fromtimestamp(msg.timestamp / 1000, tz=timezone.utc).isoformat(),
                    "value": _parse_value(msg.value),
                }
            )
            if len(messages) >= max_messages:
                return messages
    return messages


def _send_and_confirm(topic, message, key):
    producer = KafkaProducer(
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        acks="all",
    )
    try:
        future = producer.send(topic, value=message, key=key if key else None)
        return future.get(timeout=config.PRODUCE_TIMEOUT_S)
    finally:
        producer.close()


def _calculate_partition_lag(end_offsets, committed_offsets, tp_list):
    partitions = []
    for tp in tp_list:
        end_offset = end_offsets[tp]
        oam = committed_offsets.get(tp)
        committed_offset = oam.offset if oam is not None and oam.offset != -1 else 0
        partitions.append(
            {
                "partition": tp.partition,
                "end_offset": end_offset,
                "committed_offset": committed_offset,
                "lag": max(0, end_offset - committed_offset),
            }
        )
    total_lag = sum(p["lag"] for p in partitions)
    return partitions, total_lag


@mcp.tool()
def consume_topic(
    topic: str,
    max_messages: int = 20,
    timeout_ms: int = 5000,
) -> dict:
    """
    Read recent messages from a Kafka topic.

    Args:
        topic:        Kafka topic name (e.g., "system-alerts", "noc-alerts")
        max_messages: Maximum number of messages to return (default: 20, max: 100)
        timeout_ms:   Consumer poll timeout in ms (default: 5000, max: 15000)

    Returns:
        Dict with messages list: [{partition, offset, timestamp, value}]
    """
    try:
        _resolve_topic(topic, config.KAFKA_CONSUME_TOPICS, "consume")
        max_messages, orig_max = clamp(max_messages, 1, config.MAX_MESSAGES_CAP)
        timeout_ms, orig_timeout = clamp(timeout_ms, 100, config.MAX_TIMEOUT_MS_CAP)

        consumer = KafkaConsumer(
            bootstrap_servers=config.KAFKA_BOOTSTRAP,
            enable_auto_commit=False,
            value_deserializer=lambda m: m.decode("utf-8", errors="replace"),
        )
        try:
            tp_list = _seek_to_tail(consumer, topic, max_messages)
            if tp_list is None:
                available = sorted(t for t in consumer.topics() if not t.startswith("_"))
                pool = (
                    [t for t in available if t in config.KAFKA_CONSUME_TOPICS]
                    if config.KAFKA_CONSUME_TOPICS
                    else available
                )
                return format_error(
                    ValueError(f"Topic '{topic}' not found."),
                    suggestions=suggest_topics(topic, pool),
                )
            messages = _poll_messages(consumer, max_messages, timeout_ms)
        finally:
            consumer.close()

        logger.info("consume_topic topic=%s count=%d", topic, len(messages))
        result: dict = {"success": True, "topic": topic, "messages": messages, "count": len(messages)}
        clamped = {}
        if orig_max is not None:
            clamped["max_messages"] = max_messages
        if orig_timeout is not None:
            clamped["timeout_ms"] = timeout_ms
        if clamped:
            result["clamped"] = clamped
        return result

    except Exception as exc:
        logger.error("consume_topic failed for topic=%s", topic, exc_info=True)
        suggestions = suggest_topics(topic, config.KAFKA_CONSUME_TOPICS) if config.KAFKA_CONSUME_TOPICS else []
        return format_error(exc, suggestions=suggestions)


@mcp.tool()
def produce_message(
    topic: str,
    message: dict,
    key: str = "",
) -> dict:
    """
    Produce a message to a Kafka topic.

    Args:
        topic:   Target topic (e.g., "remediation-jobs", "agent-events", "incident-audit")
        message: Dict to serialize as JSON and send
        key:     Optional message key (for partitioning)

    Returns:
        Dict with delivery confirmation
    """
    try:
        _resolve_topic(topic, config.KAFKA_PRODUCE_TOPICS, "produce")
        record_metadata = _send_and_confirm(topic, message, key)

        logger.info(
            "produce_message topic=%s partition=%d offset=%d",
            record_metadata.topic,
            record_metadata.partition,
            record_metadata.offset,
        )
        return {
            "success": True,
            "topic": record_metadata.topic,
            "partition": record_metadata.partition,
            "offset": record_metadata.offset,
        }

    except Exception as exc:
        logger.error("produce_message failed for topic=%s", topic, exc_info=True)
        suggestions = suggest_topics(topic, config.KAFKA_PRODUCE_TOPICS) if config.KAFKA_PRODUCE_TOPICS else []
        return format_error(exc, suggestions=suggestions)


@mcp.tool()
def get_consumer_lag(
    group_id: str = config.DEFAULT_CONSUMER_GROUP,
    topic: str = config.DEFAULT_LAG_TOPIC,
) -> dict:
    """
    Get the current consumer lag for a consumer group.
    High lag means the agent is falling behind on processing logs.

    Args:
        group_id: Consumer group ID (default: dark-noc-agent)
        topic:    Topic to check lag for (default: nginx-logs)

    Returns:
        Dict with lag per partition and total lag
    """
    try:
        # Plain consumer (no group_id) — gets partition list and end offsets
        # without triggering the group join protocol.
        consumer = KafkaConsumer(
            bootstrap_servers=config.KAFKA_BOOTSTRAP,
            enable_auto_commit=False,
            request_timeout_ms=25_000,
        )
        try:
            partitions_set = consumer.partitions_for_topic(topic)
            if not partitions_set:
                return format_error(ValueError(f"Topic '{topic}' not found."))
            tp_list = [TopicPartition(topic, p) for p in partitions_set]
            end_offsets = consumer.end_offsets(tp_list)
        finally:
            consumer.close()

        # AdminClient fetches committed offsets directly without joining the group.
        admin = KafkaAdminClient(
            bootstrap_servers=config.KAFKA_BOOTSTRAP,
            request_timeout_ms=25_000,
            api_version_auto_timeout_ms=10_000,
        )
        try:
            committed_offsets = admin.list_consumer_group_offsets(group_id, partitions=tp_list)
        finally:
            admin.close()

        partitions, total_lag = _calculate_partition_lag(end_offsets, committed_offsets, tp_list)
        status = "healthy" if total_lag < config.CONSUMER_LAG_THRESHOLD else "behind"
        logger.info("get_consumer_lag group=%s topic=%s lag=%d status=%s", group_id, topic, total_lag, status)
        return {
            "success": True,
            "group_id": group_id,
            "topic": topic,
            "total_lag": total_lag,
            "partitions": partitions,
            "status": status,
        }

    except Exception as exc:
        logger.error("get_consumer_lag failed group=%s topic=%s", group_id, topic, exc_info=True)
        return format_error(exc)


@mcp.tool()
def list_topics() -> dict:
    """
    List all available Kafka topics.

    Returns:
        Dict with topics list: [{name, partitions}]
    """
    try:
        allowed = set(config.KAFKA_CONSUME_TOPICS) | set(config.KAFKA_PRODUCE_TOPICS)
        consumer = KafkaConsumer(bootstrap_servers=config.KAFKA_BOOTSTRAP)
        try:
            result = [
                {"name": t, "partitions": len(consumer.partitions_for_topic(t) or set())}
                for t in sorted(consumer.topics())
                if not t.startswith("_") and (not allowed or t in allowed)
            ]
        finally:
            consumer.close()

        logger.info("list_topics count=%d", len(result))
        return {"success": True, "topics": result, "count": len(result)}

    except Exception as exc:
        logger.error("list_topics failed", exc_info=True)
        return format_error(exc)
