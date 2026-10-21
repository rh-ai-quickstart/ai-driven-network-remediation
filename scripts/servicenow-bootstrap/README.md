# ServiceNow PDI Bootstrap

Automates configuring a free ServiceNow Personal Developer Instance (PDI) for the
AI-driven network remediation quickstart's incident management use case.

## Prerequisites

1. A free PDI from [developer.servicenow.com](https://developer.servicenow.com)
2. Python 3.12+ and [uv](https://docs.astral.sh/uv/)

## Quick start

```bash
# Set credentials for your PDI admin account
export SERVICENOW_INSTANCE_URL="https://dev12345.service-now.com"
export SERVICENOW_USERNAME="admin"
export SERVICENOW_PASSWORD="your-admin-password"

# Run from the repo root
make servicenow-bootstrap
```

## What it does

| Step | Description |
|------|-------------|
| 1. Create NOC Agent user | Machine user `noc_agent` with `rest_service` + `itil` roles |
| 2. Configure API access | REST API key, auth profiles, Table API access policy |
| 3. Create test data | Assignment groups (NOC-Team, Edge-Ops) and a sample incident |
| 4. Validate | Confirms incident CRUD works against the Table API |

## Validate only

If you've already bootstrapped and want to re-check connectivity:

```bash
export SERVICENOW_INSTANCE_URL="https://dev12345.service-now.com"
export SERVICENOW_USERNAME="noc_agent"
export SERVICENOW_PASSWORD="<generated password>"

make servicenow-bootstrap-validate
```

## Wake a hibernating PDI

Free PDIs hibernate after inactivity. To wake one programmatically:

```bash
export SERVICENOW_DEV_PORTAL_USERNAME="your-email@example.com"
export SERVICENOW_DEV_PORTAL_PASSWORD="your-portal-password"

make servicenow-wake
```

## Deploy with a real ServiceNow instance

After bootstrapping, copy the generated API key from `.servicenow-creds.json` (the
`api_key` field) into your environment or `.env` as `SERVICENOW_API_KEY`, set
`SERVICENOW_INSTANCE_URL`, set `ENABLE_SERVICENOW_MOCK=false`, then run
`make helm-install` (or `make deploy`). No `HELM_EXTRA_ARGS` needed.

```bash
export SERVICENOW_INSTANCE_URL="https://dev12345.service-now.com"
export SERVICENOW_API_KEY="<api_key from .servicenow-creds.json>"
export ENABLE_SERVICENOW_MOCK=false
make helm-install
```

If you do not use an API key, the MCP server falls back to Basic auth with
`SERVICENOW_USERNAME` and `SERVICENOW_PASSWORD`.
