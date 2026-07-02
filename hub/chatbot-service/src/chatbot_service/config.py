"""Configuration: environment variables and constants."""

from __future__ import annotations

import os

APP_VERSION = "0.1.0"

# ── Service URLs (quickstart in-cluster defaults) ─────────────────
MCP_OPENSHIFT_URL = os.getenv("MCP_OPENSHIFT_URL", "http://mcp-noc-openshift:8000")
MCP_LOKISTACK_URL = os.getenv("MCP_LOKISTACK_URL", "http://mcp-noc-lokistack:8000")
MCP_KAFKA_URL = os.getenv("MCP_KAFKA_URL", "http://mcp-noc-kafka:8000")
MCP_AAP_URL = os.getenv("MCP_AAP_URL", "http://mcp-noc-aap:8000")
MCP_SLACK_URL = os.getenv("MCP_SLACK_URL", "http://mcp-noc-slack:8000")
MCP_SERVICENOW_URL = os.getenv("MCP_SERVICENOW_URL", "http://mcp-noc-servicenow:8000")

SERVICENOW_URL = os.getenv("SERVICENOW_URL", "http://servicenow-mock:8080")
SERVICENOW_API_KEY = os.getenv("SERVICENOW_API_KEY", "")
SERVICENOW_MODE = os.getenv("SERVICENOW_MODE", "mock").lower()
SERVICENOW_USERNAME = os.getenv("SERVICENOW_USERNAME", "")
SERVICENOW_PASSWORD = os.getenv("SERVICENOW_PASSWORD", "")

MODEL_API_URL = os.getenv("MODEL_API_URL", "http://llamastack-service:8321/v1/completions")
MODEL_NAME = os.getenv("MODEL_NAME", "granite-4-h-tiny")
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "20"))
MODEL_MAX_TOKENS = int(os.getenv("MODEL_MAX_TOKENS", "280"))

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
DEMO_TOPIC = os.getenv("DEMO_TOPIC", "system-alerts")
AUDIT_TOPIC = os.getenv("AUDIT_TOPIC", "incident-audit")
AUDIT_LOOKBACK_HOURS = int(os.getenv("AUDIT_LOOKBACK_HOURS", "24"))
AUDIT_MAX_MESSAGES = int(os.getenv("AUDIT_MAX_MESSAGES", "500"))

INTEGRATIONS_CACHE_TTL = float(os.getenv("INTEGRATIONS_CACHE_TTL", "10"))

# Business impact estimation defaults
BASELINE_MANUAL_MTTR_SECONDS = float(os.getenv("BASELINE_MANUAL_MTTR_SECONDS", "900"))
OPS_HOURLY_COST_USD = float(os.getenv("OPS_HOURLY_COST_USD", "120"))

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# TLS verification: path to CA bundle for self-signed certs, "false" to disable, empty for default.
_ssl_env = os.getenv("SSL_CA_BUNDLE", "")
if _ssl_env.lower() == "false":
    SSL_VERIFY: bool | str = False
elif _ssl_env:
    SSL_VERIFY = _ssl_env
else:
    SSL_VERIFY = True

# ── Probe targets ─────────────────────────────────────────────────
INTEGRATION_TARGETS = [
    {"id": "mcp-openshift", "name": "MCP OpenShift", "group": "mcp", "probe_url": f"{MCP_OPENSHIFT_URL}/health"},
    {"id": "mcp-lokistack", "name": "MCP LokiStack", "group": "mcp", "probe_url": f"{MCP_LOKISTACK_URL}/health"},
    {"id": "mcp-kafka", "name": "MCP Kafka", "group": "mcp", "probe_url": f"{MCP_KAFKA_URL}/health"},
    {"id": "mcp-aap", "name": "MCP AAP", "group": "mcp", "probe_url": f"{MCP_AAP_URL}/health"},
    {"id": "mcp-slack", "name": "MCP Slack", "group": "mcp", "probe_url": f"{MCP_SLACK_URL}/health"},
    {"id": "mcp-servicenow", "name": "MCP ServiceNow", "group": "mcp", "probe_url": f"{MCP_SERVICENOW_URL}/health"},
    {"id": "servicenow", "name": "ServiceNow", "group": "platform", "probe_url": f"{SERVICENOW_URL}/health"},
]
