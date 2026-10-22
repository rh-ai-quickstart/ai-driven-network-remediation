"""Optional bounded RCA model call and strict prediction validation."""

from __future__ import annotations

import math

import httpx
from loguru import logger
from ran_rca_service import config
from ran_rca_service.models import RCAState

# Label order mirrors telco_oran.rca; categories determine remediation routing.
RCA_CLASS_CATEGORIES = {
    "Antenna Failure": "cell_failure",
    "Co-Channel Interference (Mild)": "interference",
    "Co-Channel Interference (Severe)": "interference",
    "Faulty RF Filters (Temporal)": "rf_filter_fault",
    "Doppler Shift (Severe)": "doppler_shift",
    "Faulty Handover Algorithm (Too Frequent)": "handover_fault",
    "Buffer Overflow (Gradual Buildup)": "buffer_overflow",
    "Resource Allocation Bugs": "resource_allocation_fault",
    "High Network Congestion (Gradual Buildup)": "congestion",
    "High Network Congestion (Sudden Spike)": "congestion",
}
RCA_CLASSES = tuple(RCA_CLASS_CATEGORIES)


class ClassifierResponseError(ValueError):
    """A safe, operator-facing reason for a rejected classifier response."""


def _no_prediction(reason: str) -> dict:
    return {
        "ml_class": "",
        "ml_class_index": None,
        "ml_confidence": 0.0,
        "ml_status": "failed",
        "ml_error_reason": reason,
        "ml_steer_used": False,
    }


async def classify_node(state: RCAState) -> dict:
    if not config.CLASSIFY_INFERENCE_URL:
        return _no_prediction("classifier not configured")
    headers = {"Authorization": f"Bearer {config.CLASSIFY_TOKEN}"} if config.CLASSIFY_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                config.CLASSIFY_INFERENCE_URL,
                json={"kpi_window": state.kpi_window},
                headers=headers,
            )
            response.raise_for_status()
            prediction = response.json()
        if not isinstance(prediction, dict):
            raise ClassifierResponseError("RCA model response is not an object")
        index = prediction.get("class_index")
        label = prediction.get("class")
        confidence = prediction.get("confidence")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(RCA_CLASSES):
            raise ClassifierResponseError("invalid RCA class index")
        if label != RCA_CLASSES[index]:
            raise ClassifierResponseError("RCA class label does not match index")
        if label not in RCA_CLASS_CATEGORIES or not RCA_CLASS_CATEGORIES[label]:
            raise ClassifierResponseError("RCA category mapping missing")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ClassifierResponseError("invalid RCA confidence")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ClassifierResponseError("invalid RCA model response")
        return {
            "ml_class": label,
            "ml_class_index": index,
            "ml_confidence": confidence,
            "ml_status": "used",
            "ml_error_reason": "",
            "ml_steer_used": True,
        }
    except httpx.HTTPStatusError as exc:
        logger.exception("RCA model returned HTTP failure")
        return _no_prediction(f"classifier returned HTTP {exc.response.status_code}")
    except httpx.HTTPError:
        logger.exception("RCA model unavailable")
        return _no_prediction("classifier unavailable")
    except ClassifierResponseError as exc:
        logger.exception("RCA model call failed or returned an invalid prediction")
        return _no_prediction(str(exc))
    except (ValueError, TypeError, KeyError):
        logger.exception("RCA model returned malformed data")
        return _no_prediction("malformed classifier response")
    except Exception:  # noqa: BLE001 - malformed model responses must stop this incident.
        logger.exception("Unexpected RCA model response failure")
        return _no_prediction("malformed classifier response")
