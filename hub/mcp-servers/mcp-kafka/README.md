# mcp-kafka

> Kafka tools for the NOC remediation agent — read logs, publish events, track consumer lag.

- - -

## What It Does

The remediation agent needs to see what's happening on Kafka in real time. This MCP server exposes Kafka operations as agent-callable tools over HTTP, so the agent can inspect log traffic and publish remediation events without knowing anything about the Kafka protocol.

| Tool | Description |
|---|---|
| `list_topics` | List all available topics with partition counts |
| `consume_topic` | Read the most recent messages from a topic |
| `produce_message` | Publish a JSON message to a topic |
| `get_consumer_lag` | Check how far behind a consumer group is |

Topic access is controlled per-direction: set `KAFKA_CONSUME_TOPICS` and `KAFKA_PRODUCE_TOPICS` to allowlist what the agent can read or write. Leave them empty to allow all topics.

- - -

## Configuration

| Variable | Default | Description |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `kafka:9092` | Kafka broker address |
| `KAFKA_CONSUME_TOPICS` | _(all)_ | Comma-separated consume allowlist |
| `KAFKA_PRODUCE_TOPICS` | _(all)_ | Comma-separated produce allowlist |
| `MCP_PORT` | `8003` | HTTP port the server listens on |
| `MCP_TRANSPORT` | `sse` | `sse` or `streamable-http` |

- - -

## Running Locally

```bash
cd hub/mcp-servers/mcp-kafka
uv sync

KAFKA_BOOTSTRAP=localhost:9092 uv run mcp-kafka
# Server available at http://localhost:8003
```

- - -

## Deployment

This server is deployed automatically when you run `make helm-install` from the project root. It runs as a pod in the hub cluster and connects to the in-cluster Kafka service (`kafka:9092`).

See the [project README](../../../../README.md) for the full deployment guide.
