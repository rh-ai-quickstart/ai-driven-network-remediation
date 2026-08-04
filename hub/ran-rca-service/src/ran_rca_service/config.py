"""ran-rca-service configuration from environment variables."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# LlamaStack
LLAMASTACK_URL = os.getenv("LLAMASTACK_URL", "http://llamastack:8321")
VECTOR_STORE_NAME = os.getenv("VECTOR_STORE_NAME", "telco_oran_docs")
GRANITE_MODEL = os.getenv("GRANITE_MODEL_NAME", "ibm-granite/granite-3.3-8b-instruct")

_llm: ChatOpenAI | None = None


def get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            base_url=f"{LLAMASTACK_URL}/v1",
            model=GRANITE_MODEL,
            api_key="unused",
        )
    return _llm

# Kafka
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_ANOMALIES_TOPIC = os.getenv("KAFKA_ANOMALIES_TOPIC", "ran-anomalies")
KAFKA_ENRICHED_TOPIC = os.getenv("KAFKA_ENRICHED_TOPIC", "ran-anomalies-enriched")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "ran-rca-service")
KAFKA_CONSUMER_ENABLED = _env_bool("KAFKA_CONSUMER_ENABLED", True)

# Buffer
RECENT_ANOMALIES_LIMIT = int(os.getenv("RECENT_ANOMALIES_LIMIT", "100"))
