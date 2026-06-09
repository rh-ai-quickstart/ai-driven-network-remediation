import os
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from agent_service.graph import build_graph
from agent_service.models import RemediationState

app = FastAPI(title=os.environ.get("APP_TITLE", "agent-service"))


class RemediateRequest(BaseModel):
    raw_event: str
    confidence_override: Optional[float] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"ready": True}


@app.post("/remediate", response_model=RemediationState)
async def remediate(request: RemediateRequest):
    graph = build_graph()
    return await graph.ainvoke(request.model_dump(exclude_none=True))


def start():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))
