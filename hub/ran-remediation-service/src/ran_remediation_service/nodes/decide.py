"""Decide node — keyword-matches root_cause/recommended_fix to AAP job template."""

from __future__ import annotations

from loguru import logger

from ran_remediation_service.models import RemediationState

_FALLBACK_TEMPLATE = "ran-generic-remediation"

# Rules checked in priority order; first match wins.
# Template names must match entries seeded in hub/infra/aap-mock/main.py.
_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["antenna", "tilt", "rsrp", "signal strength", "misalignment"], "ran-antenna-tilt-adjust"),
    (["interference", "sinr", "noise", "beam"],                      "ran-interference-mitigation"),
    (["throughput", "scheduler", "rate", "latency", "mcs"],          "ran-scheduler-optimize"),
    (["load", " ue ", "congestion", "offload", "handover"],           "ran-load-balance"),
    (["capacity", "prb", "utilization", "expansion"],                 "ran-capacity-expand"),
    (["failure", "outage", "recovery", "restart", "down"],            "ran-cell-recovery"),
]


def _match_template(root_cause: str, recommended_fix: str) -> str:
    text = (root_cause + " " + recommended_fix).lower()
    for keywords, template in _KEYWORD_RULES:
        if any(kw in text for kw in keywords):
            return template
    return _FALLBACK_TEMPLATE


def decide_node(state: RemediationState) -> dict:
    template = _match_template(state.root_cause, state.recommended_fix)

    extra_vars = {
        "incident_id":     state.incident_id,
        "zone":            state.zone,
        "application":     state.application,
        "ad_label":        state.ad_label,
        "ad_confidence":   state.ad_confidence,
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
