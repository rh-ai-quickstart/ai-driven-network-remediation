"""Unit tests for the decide node."""

import pytest
from ran_remediation_service.models import RemediationState
from ran_remediation_service.nodes.decide import _CATEGORY_TO_TEMPLATE, _FALLBACK_TEMPLATE, decide_node


@pytest.mark.parametrize(("category", "expected_template"), [
    ("antenna_misalignment", "ran-antenna-tilt-adjust"),
    ("interference", "ran-interference-mitigation"),
    ("scheduler_degradation", "ran-scheduler-optimize"),
    ("congestion", "ran-load-balance"),
    ("capacity_exhaustion", "ran-capacity-expand"),
    ("cell_failure", "ran-cell-recovery"),
    ("unknown", "ran-generic-remediation"),
])
def test_category_selects_template(category, expected_template):
    result = decide_node(RemediationState(root_cause_category=category))

    assert result["template_name"] == expected_template


def test_unmapped_category_uses_generic_template():
    result = decide_node(RemediationState(root_cause_category="new_category"))

    assert result["template_name"] == _FALLBACK_TEMPLATE


def test_decide_node_returns_template_and_extra_vars():
    state = RemediationState(
        incident_id="abc123",
        zone="B",
        application="Twitch",
        ad_label="anomalous",
        ad_confidence=0.98,
        root_cause_category="antenna_misalignment",
        root_cause="signal strength degraded due to antenna misalignment",
        recommended_fix="adjust tilt",
    )
    result = decide_node(state)

    assert result["template_name"] == "ran-antenna-tilt-adjust"
    assert result["extra_vars"]["root_cause_category"] == "antenna_misalignment"
    assert result["extra_vars"]["incident_id"] == "abc123"


def test_category_templates_are_unique():
    assert len(_CATEGORY_TO_TEMPLATE.values()) == len(set(_CATEGORY_TO_TEMPLATE.values()))
