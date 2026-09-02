"""
RAN Chatbot Entrypoint (Telco O-RAN)
=====================================
Thin conversational BFF for the Telco O-RAN anomaly detection and root cause
analysis use case. Delegates all domain logic (anomaly detection, root cause
analysis, recommended fix retrieval) to upstream services — this service only
handles the conversational interface and reply formatting.

Anomaly data comes from ran-rca-service, which publishes enriched anomalies
(root_cause + recommended_fix added) to the `ran-anomalies-enriched` Kafka
topic. A background thread (AnomaliesConsumer, see kafka.py) continuously
fills an in-memory buffer from that topic; request handlers just read the
buffer directly, with no per-request Kafka I/O.

Endpoints:
  GET    /health           - Liveness probe
  GET    /ready             - Readiness probe (Kafka + LLM dependency status)
  GET    /api/anomalies     - Recent enriched anomalies, newest first
  DELETE /api/anomalies     - Clear the in-memory anomaly buffer
  POST   /api/chat          - RAN anomaly chat backed by an LLM with anomaly context
  POST   /api/demo/trigger  - Publish a synthetic RAN KPI reading to the real pipeline
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from shared.probes import probe_http
from shared.utils import build_deps, normalize_session_id, utc_now

from .chat import build_chat_context, call_model, format_chat_reply
from .config import (
    APP_VERSION,
    CORS_ORIGINS,
    DEMO_METRICS_TOPIC,
    ENRICHED_ANOMALIES_MAX_MESSAGES,
    ENRICHED_ANOMALIES_TOPIC,
    KAFKA_BOOTSTRAP,
    MODEL_API_URL,
    MODEL_NAME,
    MODEL_TIMEOUT_SECONDS,
    SSL_VERIFY,
)
from .demo import DEFAULT_SCENARIO, build_demo_sample, publish_demo_metrics
from .kafka import AnomaliesConsumer
from .models import EnrichedAnomaly, ModelSource

logger = logging.getLogger(__name__)

# ── App State ─────────────────────────────────────────────────────
MAX_CHAT_SESSIONS = 100
chat_sessions: dict[str, list[dict[str, str]]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    recent_anomalies: deque[EnrichedAnomaly] = deque(maxlen=ENRICHED_ANOMALIES_MAX_MESSAGES)
    app.state.recent_anomalies = recent_anomalies

    consumer = AnomaliesConsumer(
        recent_anomalies,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        topic=ENRICHED_ANOMALIES_TOPIC,
        max_messages=ENRICHED_ANOMALIES_MAX_MESSAGES,
    )
    consumer.start()
    app.state.kafka_consumer = consumer

    # Shared across requests rather than one-per-call: httpx.AsyncClient is designed
    # for exactly this (safe for concurrent use within one event loop), and reusing
    # it gives connection pooling/keep-alive to MODEL_API_URL instead of a fresh
    # TCP/TLS handshake on every /api/chat request.
    app.state.http_client = httpx.AsyncClient(timeout=MODEL_TIMEOUT_SECONDS, verify=SSL_VERIFY)

    yield

    consumer.stop()
    await app.state.http_client.aclose()


app = FastAPI(
    title="RAN Chatbot BFF",
    version=APP_VERSION,
    description="Thin conversational entrypoint for the Telco O-RAN anomaly detection and root cause analysis use case",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request/Response Models ───────────────────────────────────────


class ChatRequest(BaseModel):
    message: str = Field(max_length=1000)
    session_id: str | None = None


class DemoTriggerRequest(BaseModel):
    scenario: str = DEFAULT_SCENARIO


# ── Endpoints ─────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ran-chatbot-bff", "version": APP_VERSION}


@app.get("/ready")
async def ready(request: Request):
    """Readiness probe — reports dependency status but always passes.

    The BFF gracefully degrades when dependencies are unavailable (fallback
    chat, empty anomaly list), so it can always serve useful traffic. Dependency
    status is informational.
    """
    checks: dict[str, bool] = {"kafka": request.app.state.kafka_consumer.is_connected}

    llm_probe = await probe_http(MODEL_API_URL, timeout=2.0, verify=SSL_VERIFY)
    checks["llm"] = llm_probe["reachable"]

    return {"status": "ready", "checks": checks}


@app.get("/api/anomalies")
def anomalies(request: Request) -> dict:
    """Recently detected RAN anomalies, newest first.

    Reads the same in-memory buffer /api/chat uses for LLM context — an
    instant operation, no Kafka I/O on the request path.
    """
    # The buffer is in ascending Kafka offset order (oldest first, see kafka.py),
    # so reversing it puts the newest anomaly first for display.
    recent = list(reversed(request.app.state.recent_anomalies))
    kafka_ok = request.app.state.kafka_consumer.is_connected

    return {
        "_deps": build_deps({"kafka": kafka_ok}),
        "count": len(recent),
        "anomalies": [a.model_dump() for a in recent],
    }


@app.delete("/api/anomalies")
def clear_anomalies(request: Request) -> dict:
    """Clear the in-memory anomaly buffer, e.g. for a clean demo/UI state.

    Only clears what's currently buffered in this process. It does not
    survive a restart or Kafka reconnect: AnomaliesConsumer's
    _seed_recent_history() re-drains the same recent window from
    ran-anomalies-enriched (7-day retention by default) every time it
    (re)connects, so old anomalies still on that topic will resurface then.
    """
    request.app.state.recent_anomalies.clear()
    kafka_ok = request.app.state.kafka_consumer.is_connected

    return {
        "_deps": build_deps({"kafka": kafka_ok}),
        "status": "cleared",
        "count": 0,
    }


@app.post("/api/demo/trigger")
async def trigger_demo(req: DemoTriggerRequest, request: Request) -> dict:
    """Publish a TelecomTS fixture sample straight to ran-combined-metrics —
    the same real input topic ran-anomaly-detector consumes from. Everything
    downstream (detection -> RCA -> this service's own buffer) is the real,
    already-running pipeline; this only injects one fresh sample into it.
    """
    json_blob, meta = build_demo_sample(req.scenario)
    try:
        offset = await asyncio.to_thread(publish_demo_metrics, json_blob)
    except Exception:
        logger.exception("Failed to publish demo RAN metrics for scenario=%s", req.scenario)
        return JSONResponse(
            status_code=502,
            content={
                "timestamp": utc_now(),
                "status": "error",
                "error": "Failed to publish demo metrics to Kafka",
                "scenario": meta["scenario"],
                "incident_id": meta["incident_id"],
            },
        )

    kafka_ok = request.app.state.kafka_consumer.is_connected
    return {
        "_deps": build_deps({"kafka": kafka_ok}),
        "timestamp": utc_now(),
        "status": "queued",
        "scenario": meta["scenario"],
        "incident_id": meta["incident_id"],
        "topic": DEMO_METRICS_TOPIC,
        "kafka_offset": offset,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> dict:
    msg = req.message.strip()
    if not msg:
        return {"reply": "Please enter a question.", "session_id": normalize_session_id(req.session_id)}

    session_id = normalize_session_id(req.session_id)
    if session_id not in chat_sessions and len(chat_sessions) >= MAX_CHAT_SESSIONS:
        oldest = next(iter(chat_sessions))
        del chat_sessions[oldest]
    history = chat_sessions.setdefault(session_id, [])

    # The background AnomaliesConsumer keeps this buffer filled, so reading it here
    # is an instant in-memory operation — no Kafka I/O on the request path at all.
    anomalies = list(request.app.state.recent_anomalies)
    kafka_ok = request.app.state.kafka_consumer.is_connected

    prompt = build_chat_context(msg, anomalies, history)
    raw_reply, model_source = await call_model(prompt, request.app.state.http_client)
    reply = format_chat_reply(msg, raw_reply, anomalies)

    history.append({"role": "user", "content": msg})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 20:
        del history[:-20]

    # Anything other than a genuine live reply (unreachable, disabled, empty, or an
    # http-<code> error) means the operator got a deterministic fallback, not a real
    # model answer, so it should be surfaced as degraded rather than "ok".
    llm_ok = model_source == ModelSource.LIVE
    _deps = build_deps({"kafka": kafka_ok, "llm": llm_ok})

    return {
        "_deps": _deps,
        "session_id": session_id,
        "timestamp": utc_now(),
        "reply": reply,
        "model": {
            "name": MODEL_NAME,
            "source": model_source,
        },
        "context": {
            "anomaly_count": len(anomalies),
        },
    }


# ── Entrypoint ────────────────────────────────────────────────────


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
