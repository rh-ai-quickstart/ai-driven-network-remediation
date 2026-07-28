# MCP AAP

MCP server wrapping the Ansible Automation Platform REST API for the AI-driven network remediation agent.

## Tools

| Tool | Description |
|---|---|
| `list_job_templates` | List available Ansible job templates |
| `launch_job` | Trigger a job template execution by name |
| `upsert_job_template` | Create or update a template for a playbook path |
| `commit_playbook` | Commit a generated playbook file to the Gitea repository |
| `sync_project` | Trigger an AAP project sync and wait for completion |
| `get_job_status` | Poll job completion status |
| `get_job_output` | Get stdout from a completed or failed job |

## Environment Variables

| Variable | Required | Default |
|---|---|---|
| `AAP_TOKEN` | Yes | — |
| `AAP_URL` | No | `https://aap.aap.svc` |
| `AAP_API_PREFIX` | No | `/api/controller/v2` |
| `AAP_VERIFY_SSL` | No | `true` |
| `GITEA_URL` | No | `http://gitea.hub.svc:3000` |
| `GITEA_OWNER` | No | `noc` |
| `GITEA_REPO` | No | `generated-playbooks` |
| `GITEA_TOKEN` | Yes (for `commit_playbook`) | — |
| `MCP_TRANSPORT` | No | `sse` |
| `MCP_PORT` | No | `8000` |

`AAP_TOKEN` is an OAuth2 Personal Access Token created in AAP. See the main [README](../../../README.md) for setup instructions.

## Running Locally

```bash
export AAP_TOKEN=your-oauth2-token
export AAP_URL=http://localhost:8082  # point at an AAP mock or real controller
export AAP_VERIFY_SSL=false
export MCP_TRANSPORT=streamable-http
uv run uvicorn mcp_aap:app --host 0.0.0.0 --port 8000
```

## Tests

```bash
# Unit tests (mocks all HTTP calls)
AAP_TOKEN=test uv sync --group dev && uv run pytest

# Integration tests run via: make integration-tests
```
