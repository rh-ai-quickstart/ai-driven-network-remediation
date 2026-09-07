"""FastAPI app: /v1/classify endpoint + health/readiness probes."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from ran_ml_service.config import MANTIS_MODEL_PATH, MLFLOW_MODEL_URI
from ran_ml_service.predictor import ClassifyPredictor, EXPECTED_CHANNELS, EXPECTED_TIMESTEPS


@asynccontextmanager
async def lifespan(app: FastAPI):
    predictor = ClassifyPredictor()

    if MANTIS_MODEL_PATH:
        try:
            predictor.load_local(MANTIS_MODEL_PATH)
        except Exception:
            logger.exception("Failed to load model from MANTIS_MODEL_PATH={}", MANTIS_MODEL_PATH)
    elif MLFLOW_MODEL_URI:
        try:
            predictor.load_mlflow(MLFLOW_MODEL_URI)
        except Exception:
            logger.exception("Failed to load model from MLFLOW_MODEL_URI={}", MLFLOW_MODEL_URI)
    else:
        logger.warning("No model path configured (set MANTIS_MODEL_PATH or MLFLOW_MODEL_URI)")

    app.state.predictor = predictor
    yield


app = FastAPI(title=os.environ.get("APP_TITLE", "ran-ml-service"), lifespan=lifespan)


class ClassifyRequest(BaseModel):
    kpi_window: list[list[float]] = Field(
        ...,
        min_length=EXPECTED_TIMESTEPS,
        max_length=EXPECTED_TIMESTEPS,
        description=f"Time series matrix ({EXPECTED_TIMESTEPS}×{EXPECTED_CHANNELS})",
    )


class ClassifyResponse(BaseModel):
    class_: str = Field(alias="class")
    confidence: float
    class_index: int

    model_config = {"populate_by_name": True}


@app.post("/v1/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest):
    predictor: ClassifyPredictor = app.state.predictor
    if not predictor.is_ready:
        return JSONResponse({"error": "Model not loaded"}, status_code=503)

    try:
        result = predictor.predict(req.kpi_window)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        logger.exception("Classify inference failed")
        return JSONResponse({"error": "Inference failed"}, status_code=500)

    return result


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    predictor: ClassifyPredictor = app.state.predictor
    if not predictor.is_ready:
        return JSONResponse({"ready": False, "reason": "model not loaded"}, status_code=503)
    return {"ready": True}


def start():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8004")))
