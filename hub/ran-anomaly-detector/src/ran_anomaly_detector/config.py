"""ran-anomaly-detector configuration from environment variables."""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Kafka
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_METRICS_TOPIC = os.getenv("KAFKA_METRICS_TOPIC", "ran-combined-metrics")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "ran-anomaly-detector")
KAFKA_CONSUMER_ENABLED = _env_bool("KAFKA_CONSUMER_ENABLED", True)

# Publishing
KAFKA_ANOMALIES_TOPIC = os.getenv("KAFKA_ANOMALIES_TOPIC", "ran-anomalies")
KAFKA_PRODUCER_ENABLED = _env_bool("KAFKA_PRODUCER_ENABLED", True)

# Detection
HISTORY_WINDOW_SIZE = int(os.getenv("HISTORY_WINDOW_SIZE", "10"))
RECENT_ANOMALIES_LIMIT = int(os.getenv("RECENT_ANOMALIES_LIMIT", "100"))
