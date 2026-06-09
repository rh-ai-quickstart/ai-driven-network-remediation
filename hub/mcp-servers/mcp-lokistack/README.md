# mcp-lokistack

> LokiStack tools for the remediation agent - search logs, query metrics, and find error patterns.

- - -

## What It Does

The remediation agent needs to inspect cluster logs stored in LokiStack. This MCP server exposes LogQL operations as agent-callable tools over HTTP, so the agent can search logs, track error rates, and surface recurring problems without constructing raw LogQL.

| Tool | Description |
|---|---|
| `search_logs` | Search logs by namespace, pod, container, and literal text |
| `search_logs_regex` | Search logs with regex pattern matching |
| `query_logql` | Execute raw LogQL queries for advanced use cases |
| `query_metrics` | Time-series metrics from logs (error rates, log volume) |
| `find_error_patterns` | Group and rank recurring error patterns |

All tools support LokiStack's three tenants: `application`, `infrastructure`, and `audit`.

- - -

## Configuration

| Variable | Default | Description |
|---|---|---|
| `LOKI_URL` | `https://logging-loki-gateway-http.openshift-logging.svc:8080` | LokiStack gateway URL |
| `LOKI_TOKEN` | — | Bearer token for authentication |
| `LOKI_TOKEN_PATH` | — | Path to a file containing the bearer token |
| `LOKI_TLS_VERIFY` | `false` | Verify TLS certificates |
| `LOKI_CA_CERT_PATH` | — | Custom CA certificate path |
| `LOKI_DEFAULT_TENANT` | `application` | Default tenant for queries |
| `LOKI_MAX_LINES` | `100` | Default max lines per query |
| `LOKI_MAX_LINES_CEILING` | `500` | Hard cap on lines per query |
| `LOKI_MAX_DURATION` | `24h` | Maximum look-back window |
| `LOKI_QUERY_TIMEOUT` | `30` | Query timeout in seconds |
| `LOKI_RETRY_ATTEMPTS` | `3` | Retry count for transient failures |
| `MCP_PORT` | `8002` | HTTP port the server listens on |
| `MCP_TRANSPORT` | `sse` | `sse`, `stdio`, or `streamable-http` |


- - -

## Deployment

This server is deployed automatically when you run `make helm-install` from the project root. It runs as a pod in the hub cluster and connects to the in-cluster LokiStack gateway. Authentication is handled via a ServiceAccount token stored in a Kubernetes secret.

See the [project README](../../../README.md) for the full deployment guide.
