"""Agent service configuration from environment variables."""

from __future__ import annotations

import os
from datetime import datetime, timezone

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

# LangGraph invoke from Kafka consumer thread (seconds; demo target is under 5 minutes).
# float (not int) so tests can use sub-second values like 0.01 with future.result(timeout=...).
GRAPH_INVOKE_TIMEOUT_SECONDS = float(os.getenv("GRAPH_INVOKE_TIMEOUT_SECONDS", "300"))

# LlamaStack
LLAMASTACK_HOST = os.environ.get("LLAMASTACK_HOST", "localhost")
LLAMASTACK_PORT = os.environ.get("LLAMASTACK_PORT", "8321")

# Lightspeed (Ansible Lightspeed playbook generation)
LIGHTSPEED_URL = os.getenv("LIGHTSPEED_URL", "")
LIGHTSPEED_TOKEN = os.getenv("LIGHTSPEED_TOKEN", "")
LIGHTSPEED_VERIFY_SSL = os.getenv("LIGHTSPEED_VERIFY_SSL", "false").lower() == "true"

# Configurable via env var to allow prompt experimentation without redeploying
LIGHTSPEED_PROMPT_TEMPLATE = os.getenv(
    "LIGHTSPEED_PROMPT_TEMPLATE",
    "The following issue was detected in an OpenShift cluster. "
    "Analyze the problem based on the description and findings below, "
    "then generate an Ansible playbook that REMEDIATES (fixes) the issue. "
    "Do NOT generate an investigation or diagnostic playbook.\n\n"
    "Failure type: {failure_type}\n"
    "Severity: {severity}\n"
    "Namespace: {namespace}\n"
    "Pod: {pod_name}\n"
    "Summary: {summary}\n\n"
    "Problem description and findings:\n{evidence}\n\n"
    "Recommended actions: {recommended_actions}\n\n"
    "Return ONLY a valid Ansible YAML playbook, no explanation.",
)

LIGHTSPEED_WRAPPER_PLAYBOOK = os.getenv(
    "LIGHTSPEED_WRAPPER_PLAYBOOK",
    "playbooks/lightspeed-generate-and-run.yaml",
)
AAP_LIGHTSPEED_TEMPLATE = os.getenv(
    "AAP_LIGHTSPEED_TEMPLATE",
    "lightspeed-runner",
)

HTTP_TIMEOUT_SECONDS = 30
LIGHTSPEED_TIMEOUT_SECONDS = 60

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=f"http://{LLAMASTACK_HOST}:{LLAMASTACK_PORT}",
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    return _http_client


# AAP job polling
TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})
POLL_INTERVAL_SECONDS = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
