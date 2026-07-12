# Graph Nodes

The agent service processes incidents through a LangGraph state machine. Each node reads from and writes to `IncidentState`.

<p align="center">
  <img src="./images/graph.png" alt="Graph" />
</p>

## Node Reference

### normalize

Parses `raw_event` into a structured `LogEvent` (namespace, pod, container, severity).

### rag_retrieval

Queries the knowledge base for runbook snippets relevant to the log event. Populates `context_snippets` and `rag_query_used`.

### analyze

Performs root cause analysis using an LLM. Produces a `RootCauseAnalysis` with `failure_type`, `confidence`, and `recommended_actions`.

### decide

Routes the incident based on confidence thresholds and failure type:

| Condition | Route |
|---|---|
| confidence >= `remediate_threshold` and known playbook type | `remediate` |
| confidence >= `remediate_threshold` and generation type | `lightspeed` |
| confidence < `escalate_threshold` | `escalate` |
| otherwise (between thresholds, unknown type) | `escalate` |

Thresholds are configurable via `GraphConfig`.

### remediate

Runs an AAP (Ansible Automation Platform) job to fix the incident.

**Flow:**

1. Takes the first `recommended_action` from the RCA as the AAP job template name.
2. Launches the job via LlamaStack tool invocation with context from the log event (namespace, pod, container, edge site).
3. Polls `get_job_status` until the job reaches a terminal status or the timeout expires (`GraphConfig.job_timeout`, default 120s).
4. On success - records the result and routes to `notify`.
5. On failure - records the attempt in `failed_attempts` and sets `should_retry = True` if under `GraphConfig.max_retries` (default 1). The graph loops back to `decide` for another attempt.
6. On timeout - records `timed_out = True` and routes directly to `notify` (no retry).

**Key files:**

- `remediate.py` - node factory and AAP job lifecycle
- `config.py` - shared HTTP client, terminal statuses, poll interval
- `utils.py` - LlamaStack tool invocation helper

### lightspeed

Generates an Ansible playbook via Ansible Lightspeed (ALS) for failure types without a known runbook, then executes it through AAP.

**Flow:**

1. If `LIGHTSPEED_URL` is not configured, returns a stub result (`lightspeed-disabled`) and skips to `notify`.
2. Builds a prompt from the RCA (`failure_type`, `recommended_actions`, `summary`, `evidence`) and log event context (namespace, pod, container, edge site).
3. Sends the prompt with attachments to ALS (`POST {LIGHTSPEED_URL}/v1/query`).
4. Extracts YAML from the response, stripping markdown fences if present.
5. Derives a playbook name from the YAML `plays[0].name` or falls back to `remediate-{failure_type}-{scope}`.
6. Upserts an AAP job template using a wrapper playbook (`lightspeed-generate-and-run.yaml`) and launches the job with extra vars containing the generated YAML. Unlike `remediate`, the node does not poll for job completion.
7. Records the result in `remediation_result` (including `generated_playbook_preview`, `generated_template_id`, and job ID) and routes to `notify`.
8. On failure at any step, records a failed result with diagnostics and routes to `notify`.

**Configuration (env vars):**

| Variable | Default | Purpose |
|---|---|---|
| `LIGHTSPEED_URL` | *(empty, disables node)* | ALS endpoint |
| `LIGHTSPEED_TOKEN` | | Bearer auth token |
| `LIGHTSPEED_VERIFY_SSL` | `false` | TLS verification |

**Key files:**

- `lightspeed.py` - node factory, ALS query, YAML extraction, AAP execution
- `config.py` - Lightspeed and AAP settings

### escalate

Creates a ServiceNow incident when confidence is too low for automated remediation.

**Flow:**

1. Builds a multi-section description from the RCA (failure type, confidence, severity, summary, evidence, recommended actions), log event context, and any failed remediation attempts.
2. Maps `estimated_severity` to ServiceNow priority (critical=1, high=2, medium=3, low=4).
3. Calls `create_incident` via LlamaStack tool invocation (`_invoke_tool`) with the description and priority.
4. On success, stores the ticket number in `servicenow_ticket` and routes to `notify`.
5. On failure, stores an empty ticket with `error_message` and still routes to `notify`.

**Key files:**

- `escalate.py` - node factory, description builder, priority mapping

### notify

Sends a Slack notification summarizing the incident outcome. Messages use Block Kit formatting with a color-coded sidebar indicating severity.

**Flow:**

1. Builds a fallback plain-text summary from the incident state (severity, site, status, description, resolution).
2. Constructs Block Kit blocks displaying severity, site, timestamp, status, description, and resolution. The attachment sidebar color reflects severity:
   - Critical: `#FF0000` (red)
   - High: `#FF6600` (orange)
   - Medium: `#FFAA00` (amber)
   - Low: `#00AA00` (green)
3. If `SERVICENOW_INSTANCE_URL` is set and a `servicenow_ticket` exists, appends a clickable ticket link to the message blocks.
4. Posts the message via `slack_sdk.WebClient` (sync client wrapped in `asyncio.to_thread` to avoid blocking the event loop).
5. Returns the `slack_thread_ts` from the Slack API response, which can be used for follow-up threading.
6. When `SLACK_ENABLED` is `false`, logs the fallback text and returns an empty string instead of posting.

After completing, routes to `audit`.

**Configuration (environment variables):**

| Variable | Default | Purpose |
|---|---|---|
| `SLACK_ENABLED` | `false` | Enable or disable Slack posting |
| `SLACK_BOT_TOKEN` | | Bot OAuth token for authentication |
| `SLACK_CHANNEL` | | Target channel or channel ID |
| `SLACK_TIMEOUT_SECONDS` | `10` | API call timeout in seconds |
| `SERVICENOW_INSTANCE_URL` | *(empty)* | When set, appends a ticket link to escalation messages |

**Key files:**

- `notify.py` - Slack message builder, Block Kit formatting, node factory

### audit

Publishes the full incident record to Kafka for compliance and post-incident review.

**Flow:**

1. Assembles an audit payload from state: `incident_id`, `failure_type`, `severity`, `edge_site_id`, `remediation_action`, `remediation_success`, plus optional `ai_confidence`, `servicenow_ticket`, `aap_job_id`, and `total_duration_ms`. Failure types not in the schema enum (`OOMKilled`, `CrashLoopBackOff`, `ConfigError`, `NetworkTimeout`) are mapped to `Unknown`.
2. Computes `total_duration_ms` from `incident_start_ms` if not already set.
3. Publishes the JSON-serialized record to Kafka via `KafkaProducer` (10s send/close timeout).
4. Publish failures are logged but do not block the graph.

**Configuration (env vars):**

| Variable | Default | Purpose |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `kafka:9092` | Kafka broker address |
| `KAFKA_AUDIT_TOPIC` | `incident-audit` | Target topic |

**Key files:**

- `audit.py` - payload builder, Kafka publisher, node factory
- `contracts/incident-audit.schema.json` - JSON Schema for audit records
