"""FastAPI app: health/readiness probes + Kafka-driven RAN anomaly detection."""

from __future__ import annotations

import os
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger
from ran_anomaly_detector.config import (
    HISTORY_WINDOW_SIZE,
    KAFKA_BOOTSTRAP,
    KAFKA_CONSUMER_ENABLED,
    KAFKA_GROUP_ID,
    KAFKA_METRICS_TOPIC,
    RECENT_ANOMALIES_LIMIT,
)
from ran_anomaly_detector.detection import AnomalyDetectionService
from ran_anomaly_detector.kafka.consumer import MetricsConsumer

AnomalyBuffer = deque[dict[str, Any]]


def _handle_metrics_message(
    raw_value: bytes,
    service: AnomalyDetectionService,
    recent_anomalies: AnomalyBuffer,
) -> None:
    anomalies = service.process_message(raw_value)
    for anomaly in anomalies:
        logger.info("RAN anomaly detected: {}", anomaly)
        recent_anomalies.append(anomaly)


@asynccontextmanager
async def lifespan(app: FastAPI):
    detection_service = AnomalyDetectionService(history_size=HISTORY_WINDOW_SIZE)
    recent_anomalies: AnomalyBuffer = deque(maxlen=RECENT_ANOMALIES_LIMIT)

    app.state.detection_service = detection_service
    app.state.recent_anomalies = recent_anomalies

    consumer: MetricsConsumer | None = None
    if KAFKA_CONSUMER_ENABLED:
        consumer = MetricsConsumer(
            lambda raw_value: _handle_metrics_message(raw_value, detection_service, recent_anomalies),
            bootstrap_servers=KAFKA_BOOTSTRAP,
            topic=KAFKA_METRICS_TOPIC,
            group_id=KAFKA_GROUP_ID,
        )
        consumer.start()
        logger.info("RAN anomaly detector Kafka consumer enabled")
    else:
        logger.info("RAN anomaly detector Kafka consumer disabled")

    app.state.kafka_consumer = consumer

    yield

    if consumer is not None:
        consumer.stop()


app = FastAPI(title=os.environ.get("APP_TITLE", "ran-anomaly-detector"), lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready(req: Request):
    not_ready = []

    if KAFKA_CONSUMER_ENABLED:
        consumer: MetricsConsumer | None = getattr(req.app.state, "kafka_consumer", None)
        if consumer is None or not consumer.is_connected:
            not_ready.append("kafka")

    if not_ready:
        return JSONResponse({"ready": False, "reason": ", ".join(not_ready)}, status_code=503)
    return {"ready": True}


@app.get("/anomalies")
def anomalies(req: Request, limit: int = 50):
    """Return the most recently detected anomalies (in-memory only, not persisted)."""
    recent: AnomalyBuffer = req.app.state.recent_anomalies
    items = list(recent)[-limit:]
    return {"count": len(items), "anomalies": items}


def start():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8002")))
