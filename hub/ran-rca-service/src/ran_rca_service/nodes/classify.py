"""Optional bounded RCA model call and strict prediction validation."""

from __future__ import annotations

import math

import httpx
from loguru import logger
from ran_rca_service import config
from ran_rca_service.models import RCAState

# Mirrored from telco_oran.rca; the model-serving image must stay independent.
RCA_CLASSES = (
    "Antenna Failure", "Co-Channel Interference (Mild)",
    "Co-Channel Interference (Severe)", "Faulty RF Filters (Temporal)",
    "Doppler Shift (Severe)", "Faulty Handover Algorithm (Too Frequent)",
    "Buffer Overflow (Gradual Buildup)", "Resource Allocation Bugs",
    "High Network Congestion (Gradual Buildup)", "High Network Congestion (Sudden Spike)",
)

MIN_GUIDANCE_CONFIDENCE = 0.6


def _no_prediction(status: str) -> dict:
    return {
        "ml_class": "",
        "ml_class_index": None,
        "ml_confidence": None,
        "ml_status": status,
        "ml_steer_used": False,
    }


async def classify_node(state: RCAState) -> dict:
    if not config.CLASSIFY_INFERENCE_URL:
        return _no_prediction("disabled")
    headers = {"Authorization": f"Bearer {config.CLASSIFY_TOKEN}"} if config.CLASSIFY_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                config.CLASSIFY_INFERENCE_URL,
                json={"kpi_window": state.kpi_window},
                headers=headers,
            )
            response.raise_for_status()
            prediction = response.json()
        if not isinstance(prediction, dict):
            raise ValueError("RCA model response is not an object")
        index = prediction.get("class_index")
        label = prediction.get("class")
        confidence = prediction.get("confidence")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(RCA_CLASSES):
            raise ValueError("invalid RCA class index")
        if label != RCA_CLASSES[index]:
            raise ValueError("RCA class label does not match index")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ValueError("invalid RCA confidence")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("invalid RCA model response")
        used = confidence >= MIN_GUIDANCE_CONFIDENCE
        return {
            "ml_class": label,
            "ml_class_index": index,
            "ml_confidence": confidence,
            "ml_status": "used" if used else "low_confidence",
            "ml_steer_used": used,
        }
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        logger.exception("RCA model call failed or returned an invalid prediction")
        return _no_prediction("unavailable")
