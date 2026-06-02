"""Kafka MCP server configuration."""

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

MCP_TRANSPORT: Literal["stdio", "sse", "streamable-http"] = os.environ.get(
    "MCP_TRANSPORT", "sse"
)  # type: ignore[assignment]
MCP_PORT = int(os.environ.get("MCP_PORT", "8003"))
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")

# Empty list = allow all topics (open by default)
KAFKA_CONSUME_TOPICS: list[str] = [t.strip() for t in os.getenv("KAFKA_CONSUME_TOPICS", "").split(",") if t.strip()]
KAFKA_PRODUCE_TOPICS: list[str] = [t.strip() for t in os.getenv("KAFKA_PRODUCE_TOPICS", "").split(",") if t.strip()]

MAX_MESSAGES_CAP = 100
MAX_TIMEOUT_MS_CAP = 15_000
PRODUCE_TIMEOUT_S = 10
CONSUMER_LAG_THRESHOLD = 100
DEFAULT_CONSUMER_GROUP = "dark-noc-agent"
DEFAULT_LAG_TOPIC = "nginx-logs"

mcp = FastMCP(
    "noc-kafka",
    instructions=(
        "Kafka streaming tools for the NOC remediation agent. "
        "Use list_topics to discover available topic names before consuming or producing. "
        "Use consume_topic to read recent messages for analysis. "
        "Use produce_message to send remediation events or audit records. "
        "Use get_consumer_lag to check if the agent is keeping up with log volume."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=(MCP_TRANSPORT == "streamable-http"),
)
