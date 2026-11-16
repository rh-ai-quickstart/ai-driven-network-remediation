# Ingestion Pipeline

Syncs packaged runbooks and RAN/ORAN vendor documentation to MinIO and ingests them into Llama Stack vector stores for RAG retrieval.

## Packaged Documents

Network remediation runbooks and RAN/ORAN vendor PDFs/DOCX are committed directly to the repository under `runbooks/` and `telco-docs/` respectively. This is an explicit simplification for the quickstart. In a production environment, consider external storage (e.g., S3, Git LFS) to keep the repository lightweight.

## Repair unreadable vector-store attachments

Normal `/runbooks/ingest` and `/telco-docs/ingest` requests replace attachments in the existing store and keep its ID. An ordinary rerun repairs a partial clear or upload. In the rare case where a provider failure interrupts an individual attachment deletion, attachment metadata can become unreadable. The attachment-list request then fails, so an ordinary rerun may fail again. The ingestion service does not delete a store automatically in response to this error.

1. Port-forward the ingestion service and Llama Stack (see [REST commands](../../docs/rest/rest-commands.md)). Set `INGEST_URL=http://localhost:8000` and `LLAMASTACK_URL=http://localhost:8321` in your shell.
2. Identify the affected store by exact name (`noc_runbooks` for Network or `telco_oran_docs` for RAN). List stores with `curl -fsS "$LLAMASTACK_URL/v1/vector_stores?limit=100"`, following `has_more`/`last_id` if needed, and record its ID. Check `GET /v1/vector_stores/<ID>/files?limit=100`. A failing attachment-list response, together with an ingestion error naming that store, identifies this exceptional case. Check the Llama Stack logs for the provider error before deleting anything.
3. Deliberately delete only that recorded store ID: `curl -fsS -X DELETE "$LLAMASTACK_URL/v1/vector_stores/<ID>"`. This discards that store's current searchable corpus. Confirm the response reports `deleted: true`; if deletion fails, stop and investigate the provider rather than deleting another store.
4. Recreate and refill the store from its source: `curl -fsS -X POST "$INGEST_URL/runbooks/ingest"` for Network or `curl -fsS -X POST "$INGEST_URL/telco-docs/ingest"` for RAN. A successful response has `failed_count: 0` and returns the new `vector_store_id` in each object. If source content is missing, run the corresponding `/runbooks/sync` or `/telco-docs/sync` endpoint first.
5. Verify the new ID appears under the same exact store name, `GET /v1/vector_stores/<NEW_ID>/files` lists the expected attachments, and `POST /v1/vector_stores/<NEW_ID>/search` with `{"query":"nginx crashloop"}` (Network) or `{"query":"gNodeB"}` (RAN) returns relevant text. Make a retrieval request through the running Network agent or RAN RCA service as well; its long-lived reader should resolve the new ID after the stale-ID response.

Never use this procedure for a transient network error or an ordinary failed ingestion. Retry those after investigating the reported failure.
