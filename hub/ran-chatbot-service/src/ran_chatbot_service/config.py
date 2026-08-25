"""Configuration: environment variables and constants."""

from __future__ import annotations

import os

from shared.utils import llamastack_url_from_env

APP_VERSION = "0.1.0"

# ── Kafka ─────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")

# Topic ran-rca-service publishes LLM-enriched anomalies to (root_cause +
# recommended_fix added). See kafka.py:AnomaliesConsumer.
ENRICHED_ANOMALIES_TOPIC = os.getenv("ENRICHED_ANOMALIES_TOPIC", "ran-anomalies-enriched")
ENRICHED_ANOMALIES_MAX_MESSAGES = int(os.getenv("ENRICHED_ANOMALIES_MAX_MESSAGES", "50"))

# Topic ran-anomaly-detector consumes RAN KPI readings from (see demo.py). Demo
# trigger publishes directly here, the same real input topic real data uses.
DEMO_METRICS_TOPIC = os.getenv("DEMO_METRICS_TOPIC", "ran-combined-metrics")

# ── LLM ───────────────────────────────────────────────────────────
LLAMASTACK_URL = llamastack_url_from_env()
MODEL_API_URL = os.getenv("MODEL_API_URL", f"{LLAMASTACK_URL}/v1/completions")
MODEL_NAME = os.getenv("MODEL_NAME", "granite-4-h-tiny")
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "20"))
MODEL_MAX_TOKENS = int(os.getenv("MODEL_MAX_TOKENS", "280"))

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# TLS verification: path to CA bundle for self-signed certs, "false" to disable, empty for default.
_ssl_env = os.getenv("SSL_CA_BUNDLE", "")
if _ssl_env.lower() == "false":
    SSL_VERIFY: bool | str = False
elif _ssl_env:
    SSL_VERIFY = _ssl_env
else:
    SSL_VERIFY = True
