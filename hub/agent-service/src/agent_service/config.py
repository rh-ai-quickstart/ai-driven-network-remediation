"""Agent service configuration from environment variables."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import get_args

import httpx
from langchain_openai import ChatOpenAI

from agent_service.kafka.alerts import ALERT_TOPICS
from agent_service.models import FailureType

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
VECTOR_STORE_NAME = os.getenv("VECTOR_STORE_NAME", "noc_runbooks")
# Chunking params for vector store file ingestion (must match ingestion-pipeline defaults)
VECTOR_STORE_CHUNK_SIZE_TOKENS = int(os.getenv("VECTOR_STORE_CHUNK_SIZE_TOKENS", "800"))
VECTOR_STORE_CHUNK_OVERLAP_TOKENS = int(os.getenv("VECTOR_STORE_CHUNK_OVERLAP_TOKENS", "80"))
GRANITE_MODEL = os.environ.get("GRANITE_MODEL_NAME", "granite-4.0-8b")

_llm: ChatOpenAI | None = None


def get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            base_url=f"http://{LLAMASTACK_HOST}:{LLAMASTACK_PORT}/v1",
            model=GRANITE_MODEL,
            api_key="unused",
        )
    return _llm


# Lightspeed (Ansible Lightspeed playbook generation)
LIGHTSPEED_URL = os.getenv("LIGHTSPEED_URL", "")
LIGHTSPEED_TOKEN = os.getenv("LIGHTSPEED_TOKEN", "")
LIGHTSPEED_VERIFY_SSL = os.getenv("LIGHTSPEED_VERIFY_SSL", "false").lower() == "true"

# Configurable via env var to allow prompt experimentation without redeploying.
# Contains only static playbook-generation instructions; dynamic fields are
# assembled in _build_prompt() and prepended at runtime.
LIGHTSPEED_PROMPT_TEMPLATE = os.getenv(
    "LIGHTSPEED_PROMPT_TEMPLATE",
    "The playbook must apply a corrective fix, not investigate or diagnose.\n"
    "Include all tasks needed for a complete remediation.\n"
    "The playbook must be self-contained and executable standalone.\n\n"
    "CRITICAL REQUIREMENTS:\n\n"
    "1. validate_certs: false -- MUST appear on EVERY ansible.builtin.uri task. "
    "The cluster uses self-signed certificates.\n\n"
    "2. Kubernetes API path -- use the correct API group for each resource kind "
    "(e.g. /apis/apps/v1/ for Deployments, /api/v1/ for Pods and ConfigMaps).\n\n"
    "3. Deployment name -- when patching a pod's parent Deployment, use the "
    "injected variable directly as {{ deployment_name }}. Do NOT derive it from "
    "the pod name; the pod name may already be a bare workload name.\n\n"
    "4. Authentication -- target the spoke cluster through the ACM cluster-proxy. "
    "AAP injects these variables at launch:\n"
    "   hub_url:         cluster-proxy base URL\n"
    "   edge_site_id:    target spoke cluster id\n"
    "   token_acm:       cluster-proxy bearer token\n"
    "   deployment_name: the workload to patch\n"
    "   Build every Kubernetes API URL from the base "
    '"{{ hub_url }}/{{ edge_site_id }}" and authenticate with the header '
    'Authorization: "Bearer {{ token_acm }}".\n'
    "   Do not read tokens from files or hardcode credentials.\n\n"
    "5. Play-level settings -- Every play MUST include:\n"
    "   hosts: localhost\n"
    "   connection: local\n"
    "   gather_facts: false\n\n"
    "6. Use ansible.builtin.uri for all Kubernetes API calls "
    "(kubernetes.core is not available).\n\n"
    "7. PATCH body -- when updating an EXISTING resource, use a YAML dict and set "
    "body_format: json, Content-Type application/strategic-merge-patch+json and "
    "status_code: 200.\n\n"
    "8. Creating resources -- a strategic-merge PATCH to a named resource returns "
    "404 if it does not exist yet. To create-or-update idempotently, use "
    "server-side apply: method PATCH on the named-resource URL with query "
    "?fieldManager=ansible&force=true, Content-Type application/apply-patch+yaml, "
    "the FULL object (apiVersion, kind, metadata.name, complete spec) as the body, "
    "body_format: json, and status_code: [200, 201].\n\n"
    "Return ONLY valid Ansible YAML, no explanation or markdown fences.",
)

_FAILURE_TYPES = ", ".join(get_args(FailureType))

ANALYZE_SYSTEM_PROMPT = os.getenv(
    "ANALYZE_SYSTEM_PROMPT",
    (
        "You are a senior NOC engineer performing root cause analysis on Kubernetes log events.\n"
        "Analyze the provided log event, any retrieved runbook context and investigation evidence, "
        "then produce a structured JSON diagnosis.\n"
        "\n"
        "Valid failure_type values: {failure_types}\n"
        "Valid estimated_severity values: critical, high, medium, low\n"
        "\n"
        "IMPORTANT: recommended_actions must contain SHORT executable remediation names, "
        "not diagnostic commands.\n"
        'Use action names like: "restart nginx service", "scale up workers", '
        '"clear disk space", "fix configuration".\n'
        "Do NOT put shell commands (oc logs, kubectl describe, etc.) in recommended_actions "
        "— those are diagnostic, not remediation.\n"
        "\n"
        "When applicable, trace symptoms to their root cause. The root cause is the "
        "underlying condition that triggers the chain of failures, not the most visible symptom.\n"
        "\n"
        "Respond ONLY with valid JSON matching the provided schema."
    ).format(failure_types=_FAILURE_TYPES),
)

# Behavior/tone only. The available-tools list is appended at runtime from the
# _TOOLS definition in nodes/investigate.py so it never drifts from the real inventory.
INVESTIGATE_SYSTEM_PROMPT = os.getenv(
    "INVESTIGATE_SYSTEM_PROMPT",
    "You are a Kubernetes incident investigator. Your job is to gather evidence about "
    "an incident by calling available tools. You are NOT analyzing or deciding — "
    "just collecting facts.\n"
    "\n"
    "You may call multiple tools in a single response when it would be efficient. "
    "If one tool fails, consider using an alternative (e.g., search_logs via Loki "
    "when get_pod_logs times out).\n"
    "\n"
    "Given the log event and any enriched pod status, decide which tools to call to "
    "gather evidence. Stop when you have enough context or cannot gather more useful "
    "information.\n"
    "\n"
    "If the evidence suggests cascading failures across services, consider "
    "investigating dependent services using get_pod_spec, search_logs, or "
    "find_error_patterns to check their health and resource configuration. "
    "Prioritize investigating services you have NOT yet examined.\n"
    "\n"
    "Skip any tool call whose output has already appeared in evidence "
    "from a previous iteration to avoid redundant work.\n"
    "\n"
    "If logs or errors reference a service on a different cluster or namespace, "
    "investigate it by passing that edge_site_id and namespace to get_pod_spec, "
    "get_pod_logs, or get_events. The Loki search tools (search_logs, "
    "find_error_patterns) only cover hub logs, so use the pod tools for spoke "
    "workloads.\n"
    "\n"
    "Do NOT analyze root causes or recommend fixes — just gather raw evidence.",
)

LIGHTSPEED_SYSTEM_PROMPT = os.getenv(
    "LIGHTSPEED_SYSTEM_PROMPT",
    "IMPORTANT: Before generating a playbook, consider using available tools "
    "to gather relevant context -- such as searching documentation, checking logs, "
    "or querying metrics -- if they would help produce a better remediation. "
    "Use your judgement on which tools are useful for the specific failure. "
    "Make the minimal change that resolves the issue: modify only the specific "
    "fields that are misconfigured and leave the rest of the resource unchanged.",
)

LIGHTSPEED_SUMMARIZE_PROMPT = os.getenv(
    "LIGHTSPEED_SUMMARIZE_PROMPT",
    "You are a Kubernetes incident analyst preparing a remediation brief.\n"
    "\n"
    "Given the root cause analysis and investigation evidence below, identify "
    "the component that needs the fix (which may differ from the alerting "
    "component), the root cause, and the remediation steps.\n"
    "\n"
    "Respond with a JSON object:\n"
    '{{"affected_component": "<service/pod/resource to fix>", '
    '"root_cause": "<one-sentence root cause>", '
    '"remediation_steps": "<comma-separated corrective actions>", '
    '"edge_site_id": "<cluster/site id if a different cluster is implicated, else empty>"}}\n'
    "\n"
    "Root cause analysis:\n"
    "{rca_summary}\n"
    "\n"
    "Evidence:\n"
    "{rca_evidence}\n"
    "\n"
    "Recommended actions: {recommended_actions}\n"
    "\n"
    "Investigation evidence:\n"
    "{investigation_evidence}",
)

AAP_LIGHTSPEED_TEMPLATE = os.getenv(
    "AAP_LIGHTSPEED_TEMPLATE",
    "lightspeed-runner",
)
GITEA_PROJECT_NAME = os.getenv("GITEA_PROJECT_NAME", "lightspeed-generated")
LIGHTSPEED_SKIP_AAP = _env_bool("LIGHTSPEED_SKIP_AAP", False)

HTTP_TIMEOUT_SECONDS = 30
LIGHTSPEED_TIMEOUT_SECONDS = int(os.getenv("LIGHTSPEED_TIMEOUT_SECONDS", "180"))
# Max chars of investigation evidence sent to the summarizer LLM (~10k tokens at 40k chars).
LIGHTSPEED_MAX_SUMMARIZE_CHARS = int(os.getenv("LIGHTSPEED_MAX_SUMMARIZE_CHARS", "40000"))

# Slack notifications
SLACK_ENABLED = _env_bool("SLACK_ENABLED", False)
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#ai-driven-network")
SLACK_TIMEOUT_SECONDS = int(os.getenv("SLACK_TIMEOUT_SECONDS", "10"))
SERVICENOW_INSTANCE_URL = os.getenv("SERVICENOW_INSTANCE_URL", "")
SERVICENOW_CREATE_RESOLVED = _env_bool("SERVICENOW_CREATE_RESOLVED", False)

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


# Spoke edge fast-path healer coordination
FAST_PATH_LAST_HEAL_ANNOTATION = "adnr.io/fast-path-last-heal"
FAST_PATH_COOLDOWN_SECONDS = int(os.getenv("FAST_PATH_COOLDOWN_SECONDS", "300"))
