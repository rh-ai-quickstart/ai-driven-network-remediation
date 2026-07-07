import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from loguru import logger
from pydantic import BaseModel

from agent_service.config import (
    GRAPH_INVOKE_TIMEOUT_SECONDS,
    KAFKA_BOOTSTRAP,
    KAFKA_CONSUMER_ENABLED,
    KAFKA_CONSUME_TOPICS,
    KAFKA_GROUP_ID,
)
from agent_service.graph import build_graph
from agent_service.kafka.consumer import AlertConsumer, AlertMessage
from agent_service.models import FailureType, IncidentState


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
    future = asyncio.run_coroutine_threadsafe(
        graph.ainvoke(
            {
                "raw_event": alert.raw_event,
                "kafka_offset": alert.offset,
            }
        ),
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
def ready():
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
