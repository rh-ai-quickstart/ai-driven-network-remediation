# MCP ServiceNow

MCP server wrapping the ServiceNow REST API for incident management in the AI-driven network remediation agent.

## Tools

| Tool | Description |
|---|---|
| `create_incident` | Open a new ServiceNow incident ticket |
| `update_incident` | Add work notes or change state on an existing incident |
| `get_incident` | Get incident details by ticket number |
| `resolve_incident` | Close an incident with resolution notes |

## Environment Variables

| Variable | Required | Default |
|---|---|---|
| `SERVICENOW_API_KEY` | Yes | — |
| `SERVICENOW_URL` | No | `http://servicenow-mock...svc:8080` |
| `SERVICENOW_MODE` | No | `auto` (`auto`/`mock`/`real`) |
| `SERVICENOW_USERNAME` | No | `""` |
| `SERVICENOW_PASSWORD` | No | `""` |
| `SERVICENOW_CALLER_NAME` | No | `NOC Agent` |
| `MCP_TRANSPORT` | No | `sse` |
| `MCP_PORT` | No | `8000` |

## Running Locally

```bash
export SERVICENOW_API_KEY=demo-api-key-2026
export SERVICENOW_URL=http://localhost:8080  # point at a servicenow-mock
export SERVICENOW_MODE=mock
export MCP_TRANSPORT=streamable-http
uv run uvicorn mcp_servicenow:app --host 0.0.0.0 --port 8000
```

## Tests

```bash
# Unit tests (mocks all HTTP calls)
SERVICENOW_API_KEY=test uv sync --group dev && uv run pytest

# Integration tests run via: make integration-tests
```
