# shared

Shared infrastructure utilities for hub services, consumed via a local `uv` path source:

- `shared.kafka.TopicConsumer` — generic threaded Kafka consumer with configurable poll timeout
  and per-message handler dispatch. Used by `ran-anomaly-detector` and `ran-rca-service`.
- `shared.rag.RagClient` — async LlamaStack vector store client with lazy vector store ID
  resolution and a negative cache. Used by `ran-rca-service` and `agent-service`.
- `shared.utils` — domain-free infra helpers (`utc_now`, `normalize_session_id`, `build_deps`, `llamastack_url_from_env`).
  Used by `chatbot-service` and `ran-chatbot-service`.
- `shared.probes` — `probe_http()`, a generic HTTP reachability probe used by `/ready` endpoints.
  Used by `chatbot-service` and `ran-chatbot-service`.

The base install (`shared`, just `httpx`) covers `shared.utils`/`shared.probes`. `shared.kafka` and
`shared.rag` pull in heavier deps (`kafka-python-ng`, `llama-stack-client` → `pandas`/`numpy`, etc.),
so they're gated behind extras — declare only what you use in `[tool.uv.sources]`:

| Consumer               | Uses                       | Declares               |
|------------------------|-----------------------------|-------------------------|
| `chatbot-service`      | `shared.utils`/`shared.probes` | `shared`             |
| `ran-chatbot-service`  | `shared.utils`/`shared.probes` | `shared`             |
| `ran-anomaly-detector` | `shared.kafka`              | `shared[kafka]`         |
| `ran-rca-service`      | `shared.kafka`, `shared.rag`| `shared[kafka,rag]`     |
| `agent-service`        | `shared.rag`                | `shared[rag]`           |

## Usage

```bash
cd hub/shared
uv sync --group dev
uv run pytest
```
