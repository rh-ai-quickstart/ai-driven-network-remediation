"""FastAPI app: health/readiness probes + Kafka-driven RAN RCA enrichment."""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from kafka import KafkaProducer
from loguru import logger

from ran_rca_service.config import (
    KAFKA_ANOMALIES_TOPIC,
    KAFKA_BOOTSTRAP,
    KAFKA_CONSUMER_ENABLED,
    KAFKA_ENRICHED_TOPIC,
    KAFKA_GROUP_ID,
    RECENT_ANOMALIES_LIMIT,
)
from ran_rca_service.graph import build_graph
from shared.kafka import TopicConsumer

EnrichedBuffer = deque[dict[str, Any]]


_consumer_loop: asyncio.AbstractEventLoop | None = None


def _get_consumer_loop() -> asyncio.AbstractEventLoop:
    global _consumer_loop
    if _consumer_loop is None or _consumer_loop.is_closed():
        _consumer_loop = asyncio.new_event_loop()
    return _consumer_loop


def _handle_anomaly_message(
    raw_value: bytes,
    graph,
    producer: KafkaProducer | None,
    enriched_topic: str,
    recent_enriched: EnrichedBuffer,
) -> None:
    try:
        anomaly = json.loads(raw_value)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Skipping malformed RAN anomaly message")
        return

    try:
        loop = _get_consumer_loop()
        result = loop.run_until_complete(graph.ainvoke(anomaly))
    except Exception:
        logger.exception("Graph invocation failed, forwarding anomaly unenriched")
        result = {**anomaly, "root_cause": "", "recommended_fix": ""}

    enriched = {
        "cell_id": result["cell_id"],
        "band": result["band"],
        "anomaly_type": result["anomaly_type"],
        "anomaly": result["anomaly"],
        "root_cause": result["root_cause"],
        "recommended_fix": result["recommended_fix"],
    }

    logger.info("RAN anomaly enriched: cell_id={} type={}", enriched["cell_id"], enriched["anomaly_type"])
    recent_enriched.append(enriched)

    if producer is not None:
        producer.send(enriched_topic, json.dumps(enriched).encode("utf-8"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    graph = build_graph()
    recent_enriched: EnrichedBuffer = deque(maxlen=RECENT_ANOMALIES_LIMIT)

    app.state.recent_enriched = recent_enriched

    producer: KafkaProducer | None = None
    consumer: TopicConsumer | None = None

    if KAFKA_CONSUMER_ENABLED:
        try:
            producer = KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
        except Exception:
            logger.warning("Could not connect Kafka producer, enriched messages will not be published")

        consumer = TopicConsumer(
            lambda raw_value: _handle_anomaly_message(
                raw_value, graph, producer, KAFKA_ENRICHED_TOPIC, recent_enriched
            ),
            name="ran-anomaly",
            bootstrap_servers=KAFKA_BOOTSTRAP,
            topic=KAFKA_ANOMALIES_TOPIC,
            group_id=KAFKA_GROUP_ID,
        )
        consumer.start()
        logger.info("RAN RCA service Kafka consumer enabled")
    else:
        logger.info("RAN RCA service Kafka consumer disabled")

    app.state.kafka_consumer = consumer
    app.state.kafka_producer = producer

    yield

    if consumer is not None:
        consumer.stop()
    if producer is not None:
        producer.close()


app = FastAPI(title=os.environ.get("APP_TITLE", "ran-rca-service"), lifespan=lifespan)


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


@app.get("/anomalies")
def anomalies(req: Request, limit: int = Query(default=50, ge=0)):
    recent: EnrichedBuffer = req.app.state.recent_enriched
    items = list(recent)[-limit:] if limit else []
    return {"count": len(items), "anomalies": items}


def start():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8003")))
