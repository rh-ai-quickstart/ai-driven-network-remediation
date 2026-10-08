"""Decide node — maps root-cause categories to AAP job templates."""

from __future__ import annotations

from loguru import logger

from ran_remediation_service.models import RemediationState

_FALLBACK_TEMPLATE = "ran-generic-remediation"

# Template names must match entries seeded in hub/infra/aap-mock/main.py.
_CATEGORY_TO_TEMPLATE = {
    "antenna_misalignment": "ran-antenna-tilt-adjust",
    "interference": "ran-interference-mitigation",
    "scheduler_degradation": "ran-scheduler-optimize",
    "congestion": "ran-load-balance",
    "capacity_exhaustion": "ran-capacity-expand",
    "cell_failure": "ran-cell-recovery",
    "unknown": _FALLBACK_TEMPLATE,
}


def decide_node(state: RemediationState) -> dict:
    template = _CATEGORY_TO_TEMPLATE.get(state.root_cause_category, _FALLBACK_TEMPLATE)

    extra_vars = {
        "incident_id":     state.incident_id,
        "zone":            state.zone,
        "application":     state.application,
        "ad_label":        state.ad_label,
        "ad_confidence":   state.ad_confidence,
        "root_cause_category": state.root_cause_category,
        "root_cause":      state.root_cause,
        "recommended_fix": state.recommended_fix,
    }

    logger.info(
        "Decide: incident_id={} zone={} → template={}",
        state.incident_id,
        state.zone,
        template,
    )
    return {"template_name": template, "extra_vars": extra_vars}
