"""ran-remediation-service configuration from environment variables."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# AAP controller REST API. Defaults to the in-namespace aap-mock; point AAP_URL
# at a real controller (and supply AAP_TOKEN) for a non-demo deployment.
AAP_URL = os.getenv("AAP_URL", "http://aap-mock:8080")
AAP_API_PREFIX = os.getenv("AAP_API_PREFIX", "/api/controller/v2")
AAP_TOKEN = os.getenv("AAP_TOKEN", "")

# Kafka
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_ENRICHED_TOPIC = os.getenv("KAFKA_ENRICHED_TOPIC", "ran-anomalies-enriched")
KAFKA_REMEDIATION_TOPIC = os.getenv("KAFKA_REMEDIATION_TOPIC", "ran-remediation-results")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "ran-remediation-service")
KAFKA_CONSUMER_ENABLED = _env_bool("KAFKA_CONSUMER_ENABLED", True)

# Slack (optional — same gating as Workflow 1)
SLACK_ENABLED = _env_bool("SLACK_ENABLED", False)
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#ran-alerts")
SLACK_TIMEOUT_SECONDS = int(os.getenv("SLACK_TIMEOUT_SECONDS", "10"))

# AAP job polling (same constants as agent-service)
TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
JOB_TIMEOUT_SECONDS = float(os.getenv("JOB_TIMEOUT_SECONDS", "120"))

# Buffer
RECENT_RESULTS_LIMIT = int(os.getenv("RECENT_RESULTS_LIMIT", "100"))

# Shared httpx client pointing at the AAP controller API
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=f"{AAP_URL.rstrip('/')}/{AAP_API_PREFIX.strip('/')}",
            headers={"Authorization": f"Bearer {AAP_TOKEN}"} if AAP_TOKEN else {},
            timeout=30.0,
        )
    return _http_client


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
