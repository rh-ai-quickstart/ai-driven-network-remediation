"""FastAPI app: health/readiness probes + ML inference endpoints."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from .config import PORT, TASK
from .model import InvalidKpiWindowError, predictor


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting ran-ml-service with TASK={}", TASK)
    try:
        predictor.load()
    except Exception:
        logger.exception("Model failed validation or loading; readiness will remain false")
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


class ClassifyResponse(BaseModel):
    class_: str = Field(alias="class")
    confidence: float
    class_index: int


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


@app.post("/v1/detect", response_model=DetectResponse)
def detect(req: DetectRequest) -> DetectResponse | JSONResponse:
    """Binary anomaly detection: is this KPI window anomalous or normal?"""
    if not predictor.is_ready:
        return JSONResponse(
            {"error": "model not loaded"},
            status_code=503,
        )
    if TASK != "detect":
        return JSONResponse({"error": "wrong task"}, status_code=404)

    try:
        result = predictor.predict(req.kpi_window)
        return DetectResponse(**result)
    except InvalidKpiWindowError as exc:
        logger.warning("Rejecting malformed kpi_window: {}", exc)
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        logger.exception("Inference failed")
        return JSONResponse(
            {"error": "inference failed"},
            status_code=500,
        )


@app.post("/v1/classify", response_model=ClassifyResponse)
def classify(req: DetectRequest) -> ClassifyResponse | JSONResponse:
    if TASK != "classify":
        return JSONResponse({"error": "wrong task"}, status_code=404)
    if not predictor.is_ready:
        return JSONResponse({"error": "model not loaded"}, status_code=503)
    try:
        prediction = predictor.predict(req.kpi_window)
        return ClassifyResponse.model_validate({"class": prediction["label"], "confidence": prediction["confidence"], "class_index": prediction["class_index"]})
    except InvalidKpiWindowError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        logger.exception("RCA inference failed")
        return JSONResponse({"error": "inference failed"}, status_code=500)


def start():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
