"""Orchestrates ML-based anomaly detection on TelecomTS JSON samples.

Receives a JSON metrics message from Kafka (one TelecomTS sample),
POSTs the kpi_window to the ML predictor, and publishes typeless
ran-anomalies only when the predictor says anomalous.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from loguru import logger

from ran_anomaly_detector.config import DETECT_INFERENCE_URL

AnomalyOutput = dict[str, Any]

_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=10.0)
    return _http_client


class AnomalyDetectionService:
    """Stateless orchestration: JSON TelecomTS sample -> ML detect -> anomaly output.

    Each Kafka message is one complete TelecomTS sample (128 timesteps x 18 KPIs).
    The service extracts the kpi_window, calls the detect predictor via HTTP,
    and only produces output when the predictor labels the sample as anomalous.
    """

    def process_message(self, raw_value: bytes) -> list[AnomalyOutput]:
        """Decode a raw Kafka message value and run it through ML detection."""
        if not raw_value or not raw_value.strip():
            return []

        try:
            sample = json.loads(raw_value.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Skipping non-JSON or non-UTF-8 RAN metrics message")
            return []

        return self.process_sample(sample)

    def process_sample(self, sample: dict) -> list[AnomalyOutput]:
        """Process a single TelecomTS JSON sample through ML detection."""
        kpi_window = sample.get("kpi_window")
        if not kpi_window or len(kpi_window) != 128:
            logger.warning("Skipping sample with missing or invalid kpi_window (len={})", len(kpi_window) if kpi_window else 0)
            return []

        detect_result = self._call_detect(kpi_window)
        if detect_result is None:
            return []

        label = detect_result.get("label", "normal")
        confidence = detect_result.get("confidence", 0.0)

        if label != "anomalous":
            return []

        incident_id = sample.get("incident_id", str(uuid.uuid4())[:8])
        zone = sample.get("zone", "")
        application = sample.get("application", "")

        output: AnomalyOutput = {
            "incident_id": incident_id,
            "zone": zone,
            "application": application,
            "kpi_window": kpi_window,
            "ad_label": "anomalous",
            "ad_confidence": round(confidence, 4),
        }

        logger.info(
            "Anomaly detected: incident_id={} zone={} confidence={:.3f}",
            incident_id, zone, confidence,
        )
        return [output]

    def _call_detect(self, kpi_window: list[dict]) -> dict | None:
        """POST kpi_window to the detect predictor. Returns None on failure."""
        if not DETECT_INFERENCE_URL:
            logger.error("DETECT_INFERENCE_URL not configured")
            return None

        try:
            client = _get_http_client()
            resp = client.post(
                DETECT_INFERENCE_URL,
                json={"kpi_window": kpi_window},
            )
            if resp.status_code != 200:
                logger.warning("Detect predictor returned HTTP {}: {}", resp.status_code, resp.text[:200])
                return None
            return resp.json()
        except httpx.TimeoutException:
            logger.warning("Detect predictor timed out at {}", DETECT_INFERENCE_URL)
            return None
        except Exception:
            logger.exception("Failed to call detect predictor")
            return None
