"""Ingestion Pipeline for syncing runbooks and ingesting them into Llama Stack.

Endpoints:
    GET /health  - Health check
    GET /models  - List models available on the Llama Stack server
    GET /vector-store  - Ensure and summarize the configured vector store
    POST /runbooks/sync  - Sync packaged runbooks to MinIO
    POST /runbooks/ingest  - Ingest MinIO runbooks into the vector store
    GET /vector-store/files/{file_id}/content  - Fetch ingested file content

Environment Variables:
    LLAMASTACK_HOST: Llama Stack hostname (default: llamastack)
    LLAMASTACK_PORT: Llama Stack port (default: 8321)
"""

from .app import app  # noqa: F401


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
