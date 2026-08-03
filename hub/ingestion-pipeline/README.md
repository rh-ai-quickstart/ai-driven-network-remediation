# Ingestion Pipeline

Syncs packaged runbooks and RAN/ORAN vendor documentation to MinIO and ingests them into Llama Stack vector stores for RAG retrieval.

## Packaged Documents

Network remediation runbooks and RAN/ORAN vendor PDFs/DOCX are committed directly to the repository under `runbooks/` and `telco-docs/` respectively. This is an explicit simplification for the quickstart. In a production environment, consider external storage (e.g., S3, Git LFS) to keep the repository lightweight.
