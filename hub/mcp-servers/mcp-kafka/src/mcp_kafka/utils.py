"""Shared utilities for Kafka MCP tools."""

from kafka.errors import KafkaError, NoBrokersAvailable

from .validators import suggest_topics


def _resolve_topic(
    topic: str,
    allowed_topics: list[str],
    operation: str,
) -> str:
    if allowed_topics and topic not in allowed_topics:
        suggestions = suggest_topics(topic, allowed_topics)
        raise ValueError(
            f"Topic '{topic}' is not allowed for {operation}. "
            f"Allowed topics: {', '.join(sorted(allowed_topics))}."
            + (f" Did you mean: {', '.join(suggestions)}?" if suggestions else "")
        )
    return topic


def format_error(exc: Exception, suggestions: list[str] | None = None) -> dict:
    if isinstance(exc, NoBrokersAvailable):
        error_type = "connection_error"
        message = "Cannot connect to Kafka broker. Check KAFKA_BOOTSTRAP configuration and network connectivity."
    elif isinstance(exc, ValueError):
        error_type = "validation_error"
        message = str(exc)
    elif isinstance(exc, KafkaError):
        error_type = "kafka_error"
        message = str(exc)
    else:
        error_type = "unexpected_error"
        message = str(exc)

    return {
        "success": False,
        "error": error_type,
        "message": message,
        "suggestions": suggestions or [],
    }
