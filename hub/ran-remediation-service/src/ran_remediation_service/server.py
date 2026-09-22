"""FastAPI app — health/readiness probes + Kafka-driven RAN remediation.

Follows the same structure as ran-rca-service/server.py (Workflow 2) and
agent-service/server.py (Workflow 1): asynccontextmanager lifespan,
TopicConsumer background thread, in-memory result buffer.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger

from ran_remediation_service.config import (
    KAFKA_BOOTSTRAP,
    KAFKA_CONSUMER_ENABLED,
    KAFKA_ENRICHED_TOPIC,
    KAFKA_GROUP_ID,
    RECENT_RESULTS_LIMIT,
)
from ran_remediation_service.graph import build_graph
from shared.kafka import TopicConsumer

ResultBuffer = deque[dict[str, Any]]

_consumer_loop: asyncio.AbstractEventLoop | None = None


def _get_consumer_loop() -> asyncio.AbstractEventLoop:
    global _consumer_loop
    if _consumer_loop is None or _consumer_loop.is_closed():
        _consumer_loop = asyncio.new_event_loop()
    return _consumer_loop


def _handle_enriched_message(
    raw_value: bytes,
    graph,
    recent_results: ResultBuffer,
) -> None:
    try:
        anomaly = json.loads(raw_value)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Skipping malformed enriched anomaly message")
        return

    try:
        loop = _get_consumer_loop()
        result = loop.run_until_complete(graph.ainvoke(anomaly))
    except Exception:
        logger.exception("Graph invocation failed for incident_id={}", anomaly.get("incident_id"))
        return

    record = {
        "incident_id":    result.get("incident_id", anomaly.get("incident_id")),
        "zone":           result.get("zone", anomaly.get("zone")),
        "application":    result.get("application", anomaly.get("application")),
        "template_name":  result.get("template_name", ""),
        "job_id":         result.get("job_id", ""),
        "job_status":     result.get("job_status", ""),
        "success":        result.get("success", False),
        "output_summary": result.get("output_summary", ""),
        "timestamp":      result.get("timestamp", ""),
    }
    logger.info(
        "RAN remediation complete: incident_id={} zone={} success={}",
        record["incident_id"], record["zone"], record["success"],
    )
    recent_results.append(record)


@asynccontextmanager
async def lifespan(app: FastAPI):
    graph = build_graph()
    recent_results: ResultBuffer = deque(maxlen=RECENT_RESULTS_LIMIT)
    app.state.recent_results = recent_results

    consumer: TopicConsumer | None = None
    if KAFKA_CONSUMER_ENABLED:
        consumer = TopicConsumer(
            lambda raw_value: _handle_enriched_message(raw_value, graph, recent_results),
            name="ran-remediation",
            bootstrap_servers=KAFKA_BOOTSTRAP,
            topic=KAFKA_ENRICHED_TOPIC,
            group_id=KAFKA_GROUP_ID,
        )
        consumer.start()
        logger.info("RAN remediation service Kafka consumer started")
    else:
        logger.info("RAN remediation service Kafka consumer disabled")

    app.state.kafka_consumer = consumer

    yield

    if consumer is not None:
        consumer.stop()


app = FastAPI(
    title=os.environ.get("APP_TITLE", "ran-remediation-service"),
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready(req: Request):
    not_ready = []
    if KAFKA_CONSUMER_ENABLED:
        consumer: TopicConsumer | None = getattr(req.app.state, "kafka_consumer", None)
        if consumer is None or not consumer.is_connected:
            not_ready.append("kafka")
    if not_ready:
        return JSONResponse({"ready": False, "reason": ", ".join(not_ready)}, status_code=503)
    return {"ready": True}


@app.get("/remediation-results")
def remediation_results(req: Request, limit: int = Query(default=50, ge=0)):
    recent: ResultBuffer = req.app.state.recent_results
    items = list(recent)[-limit:] if limit else []
    return {"count": len(items), "results": items}


def start():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8004")))
