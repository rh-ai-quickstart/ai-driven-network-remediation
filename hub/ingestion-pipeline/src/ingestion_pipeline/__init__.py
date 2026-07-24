"""Ingestion Pipeline for syncing runbooks and vendor docs into Llama Stack.

Endpoints:
    GET /health  - Health check
    GET /models  - List models available on the Llama Stack server
    GET /vector-store  - Ensure and summarize the configured vector store
    POST /runbooks/sync  - Sync packaged runbooks to MinIO
    POST /runbooks/ingest  - Ingest MinIO runbooks into the vector store
    POST /telco-docs/sync  - Convert packaged RAN/ORAN vendor docs (PDF/DOCX/Markdown) to markdown and sync to MinIO
    POST /telco-docs/ingest  - Segment MinIO vendor doc markdown and ingest into the telco vector store
    GET /vector-store/files/{file_id}/content  - Fetch ingested file content

Environment Variables:
    LLAMASTACK_HOST: Llama Stack hostname (default: llamastack-service)
    LLAMASTACK_PORT: Llama Stack port (default: 8321)
    EMBEDDING_MODEL: Embedding model id (default: sentence-transformers/nomic-ai/nomic-embed-text-v1.5)
    CHUNK_SIZE_TOKENS: Vector store chunk size in tokens (default: 800)
    CHUNK_OVERLAP_TOKENS: Vector store chunk overlap in tokens (default: 80)
    AUTO_INGEST_ON_STARTUP: Run sync+ingest for both corpora on startup (default: true)

    VECTOR_STORE_NAME: Name of the vector store for runbooks (unset skips runbook ingestion)
    RUNBOOKS_DIR: Directory of packaged runbook markdown files (default: /app/runbooks)
    MINIO_RUNBOOK_PREFIX: MinIO object prefix for runbooks (default: runbooks/)

    TELCO_VECTOR_STORE_NAME: Name of the vector store for RAN/ORAN vendor docs (unset skips telco ingestion)
    TELCO_DOCS_DIR: Directory of packaged vendor docs, PDF/DOCX/Markdown (default: /app/telco-docs)
    MINIO_TELCO_DOCS_PREFIX: MinIO object prefix for converted vendor doc markdown (default: telco-docs/)

    MINIO_ENDPOINT: MinIO endpoint, e.g. minio:9000 (required for any MinIO sync/ingest)
    MINIO_ACCESS_KEY: MinIO access key
    MINIO_SECRET_KEY: MinIO secret key
    MINIO_BUCKET: MinIO bucket name
    MINIO_SECURE: Use HTTPS for MinIO connections (default: false)
"""

from .app import app


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
