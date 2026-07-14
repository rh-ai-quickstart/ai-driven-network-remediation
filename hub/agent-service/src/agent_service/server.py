import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel


class _ProbeFilter(logging.Filter):
    """Suppress noisy health/ready probe access logs."""

    _SUPPRESSED = ("/health", "/ready")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._SUPPRESSED)


logging.getLogger("uvicorn.access").addFilter(_ProbeFilter())

from agent_service.config import (
    GRAPH_INVOKE_TIMEOUT_SECONDS,
    KAFKA_BOOTSTRAP,
    KAFKA_CONSUME_TOPICS,
    KAFKA_CONSUMER_ENABLED,
    KAFKA_GROUP_ID,
)
from agent_service.graph import build_graph
from agent_service.kafka.consumer import AlertConsumer, AlertMessage
from agent_service.models import FailureType, IncidentState
from agent_service.utils import warm_tool_cache


def _extract_overrides(raw_event: str) -> dict:
    """Extract confidence/failure_type overrides embedded in the Kafka event JSON."""
    try:
        parsed = json.loads(raw_event)
        overrides = parsed.get("_overrides") or {}
        result: dict = {}
        if "confidence_override" in overrides:
            result["confidence_override"] = float(overrides["confidence_override"])
        if "failure_type_override" in overrides:
            result["failure_type_override"] = overrides["failure_type_override"]
        return result
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}


def _invoke_graph_for_alert(
    alert: AlertMessage,
    graph,
    loop: asyncio.AbstractEventLoop,
) -> None:
    logger.info(
        "Invoking workflow for Kafka alert topic={} offset={}",
        alert.topic,
        alert.offset,
    )
    input_state: dict = {
        "raw_event": alert.raw_event,
        "kafka_offset": alert.offset,
    }
    input_state.update(_extract_overrides(alert.raw_event))

    future = asyncio.run_coroutine_threadsafe(
        graph.ainvoke(input_state),
        loop,
    )
    try:
        result = future.result(timeout=GRAPH_INVOKE_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        logger.error(
            "Workflow timed out for Kafka alert topic={} offset={} timeout_s={}",
            alert.topic,
            alert.offset,
            GRAPH_INVOKE_TIMEOUT_SECONDS,
        )
        return
    logger.info(
        "Workflow completed for Kafka alert offset={} incident_id={}",
        alert.offset,
        result.get("incident_id"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llamastack_ready = await warm_tool_cache()

    graph = build_graph()
    app.state.graph = graph
    loop = asyncio.get_running_loop()

    consumer: AlertConsumer | None = None
    if KAFKA_CONSUMER_ENABLED:
        consumer = AlertConsumer(
            lambda alert: _invoke_graph_for_alert(alert, graph, loop),
            bootstrap_servers=KAFKA_BOOTSTRAP,
            topics=KAFKA_CONSUME_TOPICS,
            group_id=KAFKA_GROUP_ID,
        )
        consumer.start()
        logger.info("Agent service Kafka consumer enabled")
    else:
        logger.info("Agent service Kafka consumer disabled")

    app.state.kafka_consumer = consumer

    yield

    if consumer is not None:
        consumer.stop()


app = FastAPI(title=os.environ.get("APP_TITLE", "agent-service"), lifespan=lifespan)


class RemediateRequest(BaseModel):
    raw_event: str
    confidence_override: Optional[float] = None
    failure_type_override: Optional[FailureType] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready(req: Request):
    not_ready = []

    # LlamaStack: retry warm-up if it failed at startup
    if not getattr(req.app.state, "llamastack_ready", False):
        if not getattr(req.app.state, "_warming", False):
            req.app.state._warming = True
            try:
                req.app.state.llamastack_ready = await asyncio.wait_for(
                    warm_tool_cache(),
                    timeout=3,
                )
            except asyncio.TimeoutError:
                pass
            finally:
                req.app.state._warming = False
    if not req.app.state.llamastack_ready:
        not_ready.append("llamastack")

    # Kafka: only checked when consumer is enabled
    if KAFKA_CONSUMER_ENABLED:
        consumer: AlertConsumer | None = getattr(req.app.state, "kafka_consumer", None)
        if consumer is None or not consumer.is_connected:
            not_ready.append("kafka")

    if not_ready:
        return JSONResponse({"ready": False, "reason": ", ".join(not_ready)}, status_code=503)
    return {"ready": True}


@app.post("/remediate", response_model=IncidentState)
async def remediate(request: RemediateRequest, req: Request):
    # Reuse the graph compiled at startup; LangGraph compiled graphs are stateless
    # and safe to invoke concurrently with distinct input state per call.
    graph = req.app.state.graph
    return await graph.ainvoke(request.model_dump(exclude_none=True))


def start():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))
