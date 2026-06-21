"""Agent service configuration from environment variables."""

from __future__ import annotations

import os

import httpx

from agent_service.kafka.alerts import ALERT_TOPICS

_DEFAULT_CONSUME_TOPICS = ",".join(sorted(ALERT_TOPICS))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Kafka
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_CONSUME_TOPICS = _env_csv("KAFKA_CONSUME_TOPICS", _DEFAULT_CONSUME_TOPICS)
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "dark-noc-agent")
KAFKA_AUDIT_TOPIC = os.getenv("KAFKA_AUDIT_TOPIC", "incident-audit")
KAFKA_CONSUMER_ENABLED = _env_bool("KAFKA_CONSUMER_ENABLED", True)

# LlamaStack
LLAMASTACK_HOST = os.environ.get("LLAMASTACK_HOST", "localhost")
LLAMASTACK_PORT = os.environ.get("LLAMASTACK_PORT", "8321")

HTTP_TIMEOUT_SECONDS = 30

http_client = httpx.AsyncClient(
    base_url=f"http://{LLAMASTACK_HOST}:{LLAMASTACK_PORT}",
    timeout=HTTP_TIMEOUT_SECONDS,
)

# AAP job polling
TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})
POLL_INTERVAL_SECONDS = 5
