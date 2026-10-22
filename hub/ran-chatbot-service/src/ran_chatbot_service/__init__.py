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

from .chat import build_chat_context, call_model, format_chat_reply, select_anomaly
from .config import (
    APP_VERSION,
    CORS_ORIGINS,
    DEMO_METRICS_TOPIC,
    ENRICHED_ANOMALIES_MAX_MESSAGES,
    ENRICHED_ANOMALIES_TOPIC,
    INCIDENT_STATUS_TOPIC,
    KAFKA_BOOTSTRAP,
    MODEL_API_URL,
    MODEL_HEALTH_URL,
    MODEL_NAME,
    MODEL_TIMEOUT_SECONDS,
    RCA_READY_URL,
    REMEDIATION_RESULTS_MAX_MESSAGES,
    REMEDIATION_RESULTS_TOPIC,
    SSL_VERIFY,
)
from .demo import DEFAULT_SCENARIO, build_demo_sample, publish_demo_metrics
from .kafka import AnomaliesConsumer, RemediationConsumer
from .models import EnrichedAnomaly, IncidentStatus, ModelSource

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

    recent_statuses: deque[IncidentStatus] = deque(maxlen=ENRICHED_ANOMALIES_MAX_MESSAGES)
    app.state.recent_statuses = recent_statuses
    status_consumer = AnomaliesConsumer(
        recent_statuses,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        topic=INCIDENT_STATUS_TOPIC,
        max_messages=ENRICHED_ANOMALIES_MAX_MESSAGES,
        record_model=IncidentStatus,
    )
    status_consumer.start()
    app.state.status_consumer = status_consumer

    recent_remediations: deque[dict] = deque(maxlen=REMEDIATION_RESULTS_MAX_MESSAGES)
    app.state.recent_remediations = recent_remediations

    remediation_consumer = RemediationConsumer(
        recent_remediations,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        topic=REMEDIATION_RESULTS_TOPIC,
        max_messages=REMEDIATION_RESULTS_MAX_MESSAGES,
    )
    remediation_consumer.start()
    app.state.remediation_consumer = remediation_consumer

    # Shared across requests rather than one-per-call: httpx.AsyncClient is designed
    # for exactly this (safe for concurrent use within one event loop), and reusing
    # it gives connection pooling/keep-alive to MODEL_API_URL instead of a fresh
    # TCP/TLS handshake on every /api/chat request.
    app.state.http_client = httpx.AsyncClient(timeout=MODEL_TIMEOUT_SECONDS, verify=SSL_VERIFY)

    yield

    consumer.stop()
    status_consumer.stop()
    remediation_consumer.stop()
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


async def _rca_ready() -> bool:
    probe = await probe_http(RCA_READY_URL, timeout=2.0, verify=SSL_VERIFY)
    return bool(probe["http_code"] == 200)


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
    checks: dict[str, bool] = {
        "kafka": request.app.state.kafka_consumer.is_connected,
        "incident_status_kafka": request.app.state.status_consumer.is_connected,
        "remediation_kafka": request.app.state.remediation_consumer.is_connected,
    }

    llm_probe = await probe_http(MODEL_HEALTH_URL, timeout=2.0, verify=SSL_VERIFY)
    checks["llm"] = llm_probe["reachable"]
    checks["rca_service"] = await _rca_ready()

    return {"status": "ready", "checks": checks}


@app.get("/api/anomalies")
async def anomalies(request: Request) -> dict:
    """Recently detected RAN anomalies, newest first, with remediation status joined in.

    Reads both in-memory buffers — anomalies and remediation results — and joins
    them by incident_id. No Kafka I/O on the request path.
    """
    # The buffer is in ascending Kafka offset order (oldest first, see kafka.py),
    # so reversing it puts the newest anomaly first for display.
    recent = list(reversed(request.app.state.recent_anomalies))
    kafka_ok = request.app.state.kafka_consumer.is_connected
    remediation_kafka_ok = request.app.state.remediation_consumer.is_connected
    status_kafka_ok = request.app.state.status_consumer.is_connected
    rca_ok = await _rca_ready()

    # Build a lookup of the latest remediation result per incident_id.
    # Iterating forward means later entries overwrite earlier ones — most recent wins.
    remediation_index: dict[str, dict] = {}
    for r in request.app.state.recent_remediations:
        remediation_index[r["incident_id"]] = r

    status_index: dict[str, IncidentStatus] = {}
    for status in request.app.state.recent_statuses:
        status_index[status.incident_id] = status

    result = []
    for a in recent:
        status = status_index.get(a.incident_id)
        if status is not None and status.stage == "rca_classification":
            continue
        if status is not None and status.stage == "rca_explanation" and (a.root_cause or a.recommended_fix):
            continue
        data = a.model_dump()
        if data["timestamp"] is None:
            del data["timestamp"]
        data["incident_status"] = status.model_dump() if status else None
        r = remediation_index.get(a.incident_id)
        data["remediation_status"] = ("completed" if r.get("success") else "failed") if r else None
        data["remediation_job_id"] = r.get("job_id") if r else None
        data["remediation_template_name"] = r.get("template_name") if r else None
        data["remediation_job_status"] = r.get("job_status") if r else None
        data["remediation_output_summary"] = r.get("output_summary") if r else None
        data["remediation_timestamp"] = r.get("timestamp") if r else None
        data["remediation_failure_stage"] = r.get("failure_stage") if r else None
        data["remediation_failure_reason"] = r.get("failure_reason") if r else None
        result.append(data)

    failures = []
    for status in reversed(list(status_index.values())):
        if status.stage != "rca_classification":
            continue
        failures.append({
            "incident_id": status.incident_id,
            "zone": status.zone,
            "application": status.application,
            "kpi_window": [],
            "ad_label": status.ad_label,
            "ad_confidence": status.ad_confidence,
            "root_cause_category": "",
            "root_cause": "",
            "recommended_fix": "",
            "ml_class": "",
            "ml_confidence": None,
            "incident_status": status.model_dump(),
            "remediation_status": None,
            "remediation_job_id": None,
            "remediation_template_name": None,
            "remediation_job_status": None,
            "remediation_output_summary": None,
            "remediation_timestamp": None,
            "remediation_failure_stage": None,
            "remediation_failure_reason": None,
        })
    result = failures + result
    result.sort(
        key=lambda item: item["incident_status"]["timestamp"] if item["incident_status"] else item.get("timestamp") or "",
        reverse=True,
    )

    return {
        "_deps": build_deps({
            "kafka": kafka_ok,
            "remediation_kafka": remediation_kafka_ok,
            "incident_status_kafka": status_kafka_ok,
            "rca_service": rca_ok,
        }),
        "count": len(result),
        "anomalies": result,
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
    request.app.state.recent_remediations.clear()
    request.app.state.recent_statuses.clear()
    kafka_ok = request.app.state.kafka_consumer.is_connected
    remediation_kafka_ok = request.app.state.remediation_consumer.is_connected
    status_kafka_ok = request.app.state.status_consumer.is_connected

    return {
        "_deps": build_deps({
            "kafka": kafka_ok,
            "remediation_kafka": remediation_kafka_ok,
            "incident_status_kafka": status_kafka_ok,
        }),
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

    # The background AnomaliesConsumer/RemediationConsumer keep these buffers filled,
    # so reading them here is an instant in-memory operation — no Kafka I/O on the
    # request path at all.
    anomalies = list(request.app.state.recent_anomalies)
    remediations = list(request.app.state.recent_remediations)
    kafka_ok = request.app.state.kafka_consumer.is_connected

    prompt = build_chat_context(msg, anomalies, remediations, history)
    raw_reply, model_source = await call_model(prompt, request.app.state.http_client)

    selected = select_anomaly(msg, anomalies)
    explanation_unavailable = selected is not None and (not selected.root_cause or not selected.recommended_fix)
    if model_source != ModelSource.LIVE and not explanation_unavailable:
        return JSONResponse(
            status_code=503,
            content={
                "error": f"LLM inference unavailable (source: {model_source})",
                "model": {"name": MODEL_NAME, "source": model_source},
            },
        )

    reply = format_chat_reply(msg, raw_reply, anomalies)

    history.append({"role": "user", "content": msg})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 20:
        del history[:-20]

    return {
        "_deps": build_deps({"kafka": kafka_ok}),
        "session_id": session_id,
        "timestamp": utc_now(),
        "reply": reply,
        "model": {
            "name": MODEL_NAME,
            "source": model_source,
        },
        "context": {
            "anomaly_count": len(anomalies),
            "remediation_count": len(remediations),
        },
    }


# ── Entrypoint ────────────────────────────────────────────────────


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
