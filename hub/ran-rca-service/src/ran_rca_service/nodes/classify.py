"""Classify node — calls the ran-ml-service to predict the root-cause class
from a TelecomTS kpi_window, then populates the ML state fields.

Skipped entirely when MANTIS_ENABLED is false or CLASSIFY_INFERENCE_URL is
unset, leaving the ML fields at their defaults (empty class, 0.0 confidence,
ml_steer_used=False) so downstream nodes fall back to LLM-only analysis.

Graceful degradation: the node never raises.  It returns ML defaults when
the kpi_window is missing/malformed, the HTTP call fails, the returned
class_index is out of range, or the confidence is below
MANTIS_CONFIDENCE_THRESHOLD.  In the low-confidence case the predicted class
and confidence are still recorded (for observability) but ml_steer_used is
set to False so RAG retrieval and analyze run unsteered.
"""

from __future__ import annotations

import httpx
from loguru import logger
from ran_rca_service.config import CLASSIFY_INFERENCE_URL, MANTIS_CONFIDENCE_THRESHOLD, MANTIS_ENABLED
from ran_rca_service.models import RCAState
from telco_oran.domain.rca_classes import RCA_CLASSES

_CLASSIFY_TIMEOUT = 10.0

_EXPECTED_TIMESTEPS = 128
_EXPECTED_CHANNELS = 18

_ML_DEFAULTS = {"ml_root_cause_class": "", "ml_confidence": 0.0, "ml_steer_used": False}


def _is_valid_kpi_window(kpi_window: list[list[float]]) -> bool:
    if not kpi_window or len(kpi_window) != _EXPECTED_TIMESTEPS:
        return False
    return all(len(row) == _EXPECTED_CHANNELS for row in kpi_window)


async def classify_node(state: RCAState) -> dict:
    if not MANTIS_ENABLED or not CLASSIFY_INFERENCE_URL:
        return dict(_ML_DEFAULTS)

    if not _is_valid_kpi_window(state.kpi_window):
        logger.debug("kpi_window missing or not {}×{}, skipping classify", _EXPECTED_TIMESTEPS, _EXPECTED_CHANNELS)
        return dict(_ML_DEFAULTS)

    try:
        async with httpx.AsyncClient(timeout=_CLASSIFY_TIMEOUT) as client:
            resp = await client.post(
                CLASSIFY_INFERENCE_URL,
                json={"kpi_window": state.kpi_window},
            )
            resp.raise_for_status()

        body = resp.json()

        class_index = body.get("class_index")
        if class_index is None or not (0 <= class_index < len(RCA_CLASSES)):
            logger.warning("Invalid class_index {} from classify endpoint, skipping", class_index)
            return dict(_ML_DEFAULTS)

        predicted_class = RCA_CLASSES[class_index]
        confidence = body.get("confidence", 0.0)

        if confidence < MANTIS_CONFIDENCE_THRESHOLD:
            logger.info(
                "Classify confidence {:.2f} below threshold {:.2f}, recording prediction but skipping steering",
                confidence,
                MANTIS_CONFIDENCE_THRESHOLD,
            )
            return {
                "ml_root_cause_class": predicted_class,
                "ml_confidence": confidence,
                "ml_steer_used": False,
            }

        return {
            "ml_root_cause_class": predicted_class,
            "ml_confidence": confidence,
            "ml_steer_used": True,
        }
    except Exception:
        logger.exception("Classify call failed, proceeding without ML steering")
        return dict(_ML_DEFAULTS)
