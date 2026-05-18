"""Ingestion Pipeline -- Hello-world service calling Llama Stack server.

Endpoints:
    GET /health  - Health check
    GET /models  - List models available on the Llama Stack server

Environment Variables:
    LLAMASTACK_HOST: Llama Stack hostname (default: llamastack)
    LLAMASTACK_PORT: Llama Stack port (default: 8321)
"""

from .app import app  # noqa: F401


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
