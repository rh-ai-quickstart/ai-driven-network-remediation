"""Agent service configuration from environment variables."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import get_args

import httpx
from langchain_openai import ChatOpenAI

from agent_service.kafka.alerts import ALERT_TOPICS
from agent_service.models import FailureType, GraphConfig

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

# Helm supplies the enabled MCP deployments; standalone runs retain all tools.
ENABLED_MCP_SERVERS = frozenset(
    _env_csv("ENABLED_MCP_SERVERS", "noc-openshift,noc-lokistack,noc-kafka,noc-aap,noc-servicenow")
)

# LlamaStack
LLAMASTACK_HOST = os.environ.get("LLAMASTACK_HOST", "localhost")
LLAMASTACK_PORT = os.environ.get("LLAMASTACK_PORT", "8321")
VECTOR_STORE_NAME = os.getenv("VECTOR_STORE_NAME", "noc_runbooks")
# Chunking params for vector store file ingestion (must match ingestion-pipeline defaults)
VECTOR_STORE_CHUNK_SIZE_TOKENS = int(os.getenv("VECTOR_STORE_CHUNK_SIZE_TOKENS", "800"))
VECTOR_STORE_CHUNK_OVERLAP_TOKENS = int(os.getenv("VECTOR_STORE_CHUNK_OVERLAP_TOKENS", "80"))
GRANITE_MODEL = os.environ.get("GRANITE_MODEL_NAME", "")

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
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "single-cluster")
_LIGHTSPEED_AUTH_REQUIREMENTS = (
    "4. Authentication -- target the local Kubernetes API. Read the AAP-injected "
    "Kubernetes bearer-token credential using lookup('env', 'K8S_AUTH_HOST') for "
    "the API base URL and lookup('env', 'K8S_AUTH_API_KEY') for the bearer token. "
    "K8S_AUTH_HOST is a complete URL including https://; never prepend a scheme. "
    "Use Authorization: Bearer <token>. Do not append edge_site_id to the URL. "
    "Use the injected namespace and deployment_name variables directly; do not "
    "redefine them as self-referencing play variables. Never hardcode credentials.\n\n"
    if DEPLOYMENT_MODE == "single-cluster"
    else (
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
    )
)

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
    "the pod name; the pod name may already be a bare workload name. AAP already "
    "supplies deployment_name: do not discover it or assign it using set_fact. "
    "Every Deployment URL must use /deployments/{{ deployment_name }}.\n\n"
    + _LIGHTSPEED_AUTH_REQUIREMENTS
    + "5. Play-level settings -- Every play MUST include:\n"
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
    "9. Deployment restart -- patch spec.template.metadata.annotations with a fresh "
    "kubectl.kubernetes.io/restartedAt timestamp, then poll the Deployment until "
    "observedGeneration reaches the patched generation and updatedReplicas, "
    "readyReplicas and availableReplicas reach spec.replicas. Use bounded retries. "
    "Do not delete a pod and poll its old name; replacement pods have new names.\n\n"
    "10. Keep changes relevant to the diagnosed failure. Do not change CPU or "
    "memory resources for DNS failures without evidence of resource exhaustion.\n\n"
    "11. Do not call pods/exec: uri does not support Kubernetes exec streaming, and the "
    "remediation credential does not grant pods/exec. Restart containers through the "
    "Deployment and verify its rollout; do not run nslookup or systemctl in a pod.\n\n"
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

DECIDE_MATCH_PROMPT = os.getenv(
    "DECIDE_MATCH_PROMPT",
    "You are a senior SRE deciding whether a PRE-BUILT remediation playbook will "
    "actually fix a diagnosed incident. Reason step by step:\n"
    "1. State the root cause from the analysis.\n"
    "2. State what the candidate playbook does.\n"
    "3. Decide if running it resolves THIS root cause, not just the symptom.\n"
    "Choose NO_MATCH if the playbook is unrelated, only masks the symptom, or you "
    "are unsure.\n"
    "Respond ONLY with valid JSON matching the provided schema: "
    '{"reasoning": "...", "verdict": "MATCH" | "NO_MATCH"}.',
)

# Behavior/tone only. The available-tools list is appended at runtime from the
# enabled investigation tools below so it never drifts from the real inventory.
INVESTIGATE_SYSTEM_PROMPT = os.getenv(
    "INVESTIGATE_SYSTEM_PROMPT",
    "You are a Kubernetes incident investigator. Your job is to gather evidence about "
    "an incident by calling available tools. You are NOT analyzing or deciding — "
    "just collecting facts.\n"
    "\n"
    "You may call multiple tools in a single response when it would be efficient. "
    "If one tool fails, consider using an alternative from the available tools below.\n"
    "\n"
    "Given the log event and any enriched pod status, decide which tools to call to "
    "gather evidence. Stop when you have enough context or cannot gather more useful "
    "information.\n"
    "\n"
    "If the evidence suggests cascading failures across services, consider "
    "investigating dependent services using the available tools to check their "
    "health and resource configuration. "
    "Prioritize investigating services you have NOT yet examined.\n"
    "\n"
    "Skip any tool call whose output has already appeared in evidence "
    "from a previous iteration to avoid redundant work.\n"
    "\n"
    "If logs or errors reference a service on a different cluster or namespace, "
    "use available tools that accept edge_site_id and namespace to investigate it. "
    "Aggregated log search only covers hub logs; use available pod tools for spoke workloads.\n"
    "\n"
    "Do NOT analyze root causes or recommend fixes — just gather raw evidence.",
)

_INVESTIGATE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_pods",
            "description": "List pods in a namespace with status, optionally on a specified edge site",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "edge_site_id": {
                        "type": "string",
                        "description": "Edge site / cluster ID (e.g. edge-site-01). Defaults to the incident cluster.",
                    },
                },
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "Get Kubernetes events for a namespace",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "limit": {"type": "integer", "description": "Max events to return", "default": 20},
                    "edge_site_id": {
                        "type": "string",
                        "description": "Edge site / cluster ID (e.g. edge-site-01). Defaults to the incident cluster.",
                    },
                },
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_error_patterns",
            "description": "Find recurring error patterns in logs for an application",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "app": {"type": "string", "description": "Application name"},
                    "duration": {"type": "string", "description": "Time window, e.g. '1h'", "default": "1h"},
                    "top_n": {"type": "integer", "description": "Max patterns to return", "default": 10},
                    "regex": {"type": "string", "description": "Optional regex filter"},
                },
                "required": ["namespace", "app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": "Get logs from a specific pod",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Pod name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "container": {"type": "string", "description": "Container name"},
                    "tail_lines": {"type": "integer", "description": "Number of lines from the end", "default": 100},
                    "edge_site_id": {
                        "type": "string",
                        "description": "Edge site / cluster ID (e.g. edge-site-01). Defaults to the incident cluster.",
                    },
                },
                "required": ["pod_name", "namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": (
                "Search aggregated logs via Loki. Filters do NOT accept '*' or "
                "shell-glob wildcards. Prefer namespace + text; add pod only if "
                "needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "pod": {
                        "type": "string",
                        "description": "Pod name substring (partial match, no wildcards; e.g. 'edge-fast-path')",
                    },
                    "container": {
                        "type": "string",
                        "description": (
                            "Exact container name (NOT the pod name). Omit unless you know it; "
                            "it is an exact match, not a substring."
                        ),
                    },
                    "labels": {
                        "type": "object",
                        "description": "Label selector",
                        "additionalProperties": {"type": "string"},
                    },
                    "text": {"type": "string", "description": "Text to search for"},
                    "duration": {"type": "string", "description": "Time window, e.g. '1h'", "default": "1h"},
                    "limit": {"type": "integer", "description": "Max results", "default": 50},
                },
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_spec",
            "description": (
                "Get structured spec for a pod as JSON. Includes container "
                "resource limits, requests, probes, and env vars. Supports "
                "prefix matching (e.g. 'myapp' finds 'myapp-6b7f8c-x2k9z')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Pod name (exact or prefix)"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "edge_site_id": {
                        "type": "string",
                        "description": "Edge site / cluster ID (e.g. edge-site-01). Defaults to the incident cluster.",
                    },
                },
                "required": ["name", "namespace"],
            },
        },
    },
]


_INVESTIGATE_TOOL_SERVERS = {
    "get_pods": "noc-openshift",
    "get_events": "noc-openshift",
    "get_pod_logs": "noc-openshift",
    "get_pod_spec": "noc-openshift",
    "find_error_patterns": "noc-lokistack",
    "search_logs": "noc-lokistack",
}


INVESTIGATE_TOOL_SCHEMAS = {tool["function"]["name"]: tool["function"]["parameters"] for tool in _INVESTIGATE_TOOLS}
INVESTIGATE_OPENSHIFT_TOOLS = frozenset(
    tool_name for tool_name, server in _INVESTIGATE_TOOL_SERVERS.items() if server == "noc-openshift"
)


def get_investigate_tools() -> list[dict]:
    return [
        tool
        for tool in _INVESTIGATE_TOOLS
        if _INVESTIGATE_TOOL_SERVERS[tool["function"]["name"]] in ENABLED_MCP_SERVERS
    ]


def get_investigate_system_prompt(tools: list[dict]) -> str:
    tool_docs = "\n".join(
        f"- {tool['function']['name']}"
        f"({', '.join(tool['function']['parameters']['properties'])}): "
        f"{tool['function']['description']}"
        for tool in tools
    )
    return f"{INVESTIGATE_SYSTEM_PROMPT}\n\nAvailable tools:\n{tool_docs}"


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

AAP_RESOURCE_PREFIX = os.getenv("AAP_RESOURCE_PREFIX", "")
AAP_LIGHTSPEED_TEMPLATE = os.getenv(
    "AAP_LIGHTSPEED_TEMPLATE",
    "lightspeed-runner",
)
GITEA_PROJECT_NAME = os.getenv("GITEA_PROJECT_NAME", "lightspeed-generated")
LIGHTSPEED_SKIP_AAP = _env_bool("LIGHTSPEED_SKIP_AAP", False)

HTTP_TIMEOUT_SECONDS = 30
LIGHTSPEED_TIMEOUT_SECONDS = int(os.getenv("LIGHTSPEED_TIMEOUT_SECONDS", "180"))
AAP_PROJECT_SYNC_TIMEOUT_SECONDS = 60
# Assume normal runs finish within this margin; revisit if valid runs hit it.
_GRAPH_TIMEOUT_HEADROOM_FACTOR = 2
_DEFAULT_GRAPH_TIMEOUT_SECONDS = _GRAPH_TIMEOUT_HEADROOM_FACTOR * (
    GraphConfig().investigate_timeout
    + LIGHTSPEED_TIMEOUT_SECONDS
    + AAP_PROJECT_SYNC_TIMEOUT_SECONDS
    + GraphConfig().job_timeout
)
GRAPH_INVOKE_TIMEOUT_SECONDS = float(os.getenv("GRAPH_INVOKE_TIMEOUT_SECONDS", str(_DEFAULT_GRAPH_TIMEOUT_SECONDS)))
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


# Simulated edge workload namespace (Helm topology.edgeNamespace).
EDGE_NAMESPACE = os.getenv("EDGE_NAMESPACE", "dark-noc-edge")

# Spoke edge fast-path healer coordination
FAST_PATH_LAST_HEAL_ANNOTATION = "adnr.io/fast-path-last-heal"
ROLLOUT_RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"
FAST_PATH_COOLDOWN_SECONDS = int(os.getenv("FAST_PATH_COOLDOWN_SECONDS", "300"))
# Max seconds between spoke fast-path heal and dashboard OOM demo alert to skip hub AAP (UC3).
DEMO_OOM_FAST_PATH_MAX_GAP_SECONDS = int(os.getenv("DEMO_OOM_FAST_PATH_MAX_GAP_SECONDS", "900"))
