import os
from typing import Any

from fastapi import FastAPI
from llama_stack_client import LlamaStackClient

LLAMASTACK_HOST = os.environ.get("LLAMASTACK_HOST", "llamastack")
LLAMASTACK_PORT = int(os.environ.get("LLAMASTACK_PORT", "8321"))

app = FastAPI(
    title="Ingestion Pipeline",
    description="Hello-world service calling Llama Stack server",
    version="0.1.0",
)


def _get_client() -> LlamaStackClient:
    return LlamaStackClient(base_url=f"http://{LLAMASTACK_HOST}:{LLAMASTACK_PORT}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/models")
async def models() -> dict[str, Any]:
    client = _get_client()
    result = client.models.list()
    return {"models": [m.model_dump() for m in result]}
