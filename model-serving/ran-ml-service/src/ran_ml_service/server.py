"""FastAPI app: health/readiness probes + ML inference endpoints."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from .config import PORT, TASK
from .model import predictor


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting ran-ml-service with TASK={}", TASK)
    predictor.load()
    yield


app = FastAPI(
    title=os.environ.get("APP_TITLE", "ran-ml-service"),
    lifespan=lifespan,
)


class DetectRequest(BaseModel):
    kpi_window: list[dict] = Field(
        ...,
        min_length=128,
        max_length=128,
        description="128 timesteps, each with 18 TelecomTS KPI channel values",
    )


class DetectResponse(BaseModel):
    label: str
    confidence: float
    class_index: int = 0


@app.get("/health")
def health():
    return {"status": "ok", "task": TASK}


@app.get("/ready")
def ready():
    if not predictor.is_ready:
        return JSONResponse(
            {"ready": False, "reason": "model not loaded"},
            status_code=503,
        )
    return {"ready": True, "task": TASK}


@app.post("/v1/detect")
def detect(req: DetectRequest) -> DetectResponse:
    """Binary anomaly detection: is this KPI window anomalous or normal?"""
    if not predictor.is_ready:
        return JSONResponse(
            {"error": "model not loaded"},
            status_code=503,
        )

    try:
        result = predictor.predict(req.kpi_window)
        return DetectResponse(**result)
    except Exception:
        logger.exception("Inference failed")
        return JSONResponse(
            {"error": "inference failed"},
            status_code=500,
        )


def start():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
