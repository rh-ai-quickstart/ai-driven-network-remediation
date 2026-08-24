"""Classify node — calls the ran-ml-service to predict the root-cause class
from a TelecomTS kpi_window, then populates the ML state fields.

Skipped entirely when MANTIS_ENABLED is false or CLASSIFY_INFERENCE_URL is
unset, leaving the ML fields at their defaults (empty class, 0.0 confidence,
ml_steer_used=False) so downstream nodes fall back to LLM-only analysis.
"""

from __future__ import annotations

import httpx
from loguru import logger
from ran_rca_service.config import CLASSIFY_INFERENCE_URL, MANTIS_ENABLED
from ran_rca_service.models import RCAState

_CLASSIFY_TIMEOUT = 10.0

_ML_DEFAULTS = {"ml_root_cause_class": "", "ml_confidence": 0.0, "ml_steer_used": False}


async def classify_node(state: RCAState) -> dict:
    if not MANTIS_ENABLED or not CLASSIFY_INFERENCE_URL:
        return dict(_ML_DEFAULTS)

    if not state.kpi_window:
        logger.debug("No kpi_window in state, skipping classify")
        return dict(_ML_DEFAULTS)

    try:
        async with httpx.AsyncClient(timeout=_CLASSIFY_TIMEOUT) as client:
            resp = await client.post(
                CLASSIFY_INFERENCE_URL,
                json={"kpi_window": state.kpi_window},
            )
            resp.raise_for_status()

        body = resp.json()
        return {
            "ml_root_cause_class": body["class"],
            "ml_confidence": body["confidence"],
            "ml_steer_used": True,
        }
    except Exception:
        logger.exception("Classify call failed, proceeding without ML steering")
        return dict(_ML_DEFAULTS)
