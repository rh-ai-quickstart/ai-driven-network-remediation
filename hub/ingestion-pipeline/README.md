# Ingestion Pipeline

Syncs packaged runbooks and RAN/ORAN vendor documentation to MinIO and ingests them into Llama Stack vector stores for RAG retrieval.

## Packaged Documents

Network remediation runbooks and RAN/ORAN vendor PDFs/DOCX are committed directly to the repository under `runbooks/` and `telco-docs/` respectively. This is an explicit simplification for the quickstart. In a production environment, consider external storage (e.g., S3, Git LFS) to keep the repository lightweight.

## Ingestion API responses

`POST /runbooks/ingest` (Network) and `POST /telco-docs/ingest` (Telco) return the same status codes. Error responses have a JSON `detail` field.

| Status | Meaning | Caller action |
| --- | --- | --- |
| `200 OK` | Replacement completed. The response summarizes the ingested attachments. | |
| `400 Bad Request` | MinIO is incompletely configured or the source corpus is empty. | Check the MinIO settings or populate the source with the matching sync endpoint, then retry. |
| `409 Conflict` | The same corpus is already being ingested, or multiple stores match the configured name. | Read `detail`. Wait for the active run to finish before retrying, or resolve the duplicate store name before retrying. |
| `502 Bad Gateway` | Llama Stack could not list, attach, or detach files, or attachments remained after cleanup. `detail` gives the reason and identifies affected attachments where available. A failed attachment batch returns its `failed` items and their reasons. If rollback could not remove or verify any attachments, the summary also includes `rollback_errors` with their IDs. | Investigate the reported provider failure before retrying. See the repair procedure below if attachment metadata is unreadable. |
| `500 Internal Server Error` | An unexpected ingestion exception occurred. | Check the ingestion service logs for the traceback. |

## Repair unreadable vector-store attachments

Normal `/runbooks/ingest` and `/telco-docs/ingest` requests keep the store ID and leave the previous corpus searchable until all replacement attachments are ready. A failed upload triggers rollback of the new attachments. If rollback cannot remove or verify an attachment, the service logs its ID. A failed deletion of an older attachment leaves the complete replacement searchable, and a rerun can remove any leftovers.

In the rare case where a provider failure interrupts an individual attachment deletion, attachment metadata can become unreadable. The attachment-list request then fails, so an ordinary rerun may fail again. The ingestion service does not delete a store automatically in response to this error.

1. Port-forward the ingestion service and Llama Stack (see [REST commands](../../docs/rest/rest-commands.md)). Set `INGEST_URL=http://localhost:8000` and `LLAMASTACK_URL=http://localhost:8321` in your shell.
2. Identify the affected store by exact name (`noc_runbooks` for Network or `telco_oran_docs` for RAN). List stores with `curl -fsS "$LLAMASTACK_URL/v1/vector_stores?limit=100"`, following `has_more`/`last_id` if needed, and record its ID. Check `GET /v1/vector_stores/<ID>/files?limit=100`. A failing attachment-list response, together with an ingestion error naming that store, identifies this exceptional case. Check the Llama Stack logs for the provider error before deleting anything.
3. Deliberately delete only that recorded store ID: `curl -fsS -X DELETE "$LLAMASTACK_URL/v1/vector_stores/<ID>"`. This discards that store's current searchable corpus. Confirm the response reports `deleted: true`; if deletion fails, stop and investigate the provider rather than deleting another store.
4. Recreate and refill the store from its source: `curl -fsS -X POST "$INGEST_URL/runbooks/ingest"` for Network or `curl -fsS -X POST "$INGEST_URL/telco-docs/ingest"` for RAN. A successful response has `failed_count: 0` and returns the new `vector_store_id` in each object. If source content is missing, run the corresponding `/runbooks/sync` or `/telco-docs/sync` endpoint first.
5. Verify the new ID appears under the same exact store name, `GET /v1/vector_stores/<NEW_ID>/files` lists the expected attachments, and `POST /v1/vector_stores/<NEW_ID>/search` with `{"query":"nginx crashloop"}` (Network) or `{"query":"gNodeB"}` (RAN) returns relevant text. Make a retrieval request through the running Network agent or RAN RCA service as well; its long-lived reader should resolve the new ID after the stale-ID response.

Never use this procedure for a transient network error or an ordinary failed ingestion. Retry those after investigating the reported failure.
