"""Unit tests for the decide node."""

import pytest

from ran_remediation_service.models import RemediationState
from ran_remediation_service.nodes.decide import (
    _FALLBACK_TEMPLATE,
    _KEYWORD_RULES,
    _match_template,
    decide_node,
)


@pytest.mark.parametrize("root_cause,expected_template", [
    ("antenna misalignment detected",               "ran-antenna-tilt-adjust"),
    ("low rsrp due to distance",                    "ran-antenna-tilt-adjust"),
    ("sinr degradation from interference source",   "ran-interference-mitigation"),
    ("noise floor elevated, beam adjustment needed","ran-interference-mitigation"),
    ("throughput drop below threshold",             "ran-scheduler-optimize"),
    ("mcs index degraded, scheduler suboptimal",    "ran-scheduler-optimize"),
    ("ue congestion, offload recommended",          "ran-load-balance"),
    ("prb utilization at capacity limit",           "ran-capacity-expand"),
    ("cell outage detected, restart required",      "ran-cell-recovery"),
    ("cell failure, recovery procedure initiated",  "ran-cell-recovery"),
])
def test_keyword_matching(root_cause, expected_template):
    assert _match_template(root_cause, "") == expected_template


def test_fallback_on_no_keywords():
    assert _match_template("unknown issue", "no known fix") == _FALLBACK_TEMPLATE


def test_recommended_fix_also_matched():
    assert _match_template("", "antenna tilt adjustment recommended") == "ran-antenna-tilt-adjust"


def test_decide_node_returns_template_and_extra_vars():
    state = RemediationState(
        incident_id="abc123",
        zone="B",
        application="Twitch",
        ad_label="anomalous",
        ad_confidence=0.98,
        root_cause="signal strength degraded due to antenna misalignment",
        recommended_fix="adjust tilt",
    )
    result = decide_node(state)
    assert result["template_name"] == "ran-antenna-tilt-adjust"
    ev = result["extra_vars"]
    assert ev["incident_id"] == "abc123"
    assert ev["zone"] == "B"
    assert ev["application"] == "Twitch"
    assert ev["ad_confidence"] == 0.98
    assert ev["root_cause"] == "signal strength degraded due to antenna misalignment"


def test_decide_node_fallback():
    state = RemediationState(root_cause="undetermined", recommended_fix="check logs")
    result = decide_node(state)
    assert result["template_name"] == _FALLBACK_TEMPLATE


def test_all_rules_have_unique_templates():
    templates = [t for _, t in _KEYWORD_RULES]
    assert len(templates) == len(set(templates)), "Duplicate templates in _KEYWORD_RULES"
