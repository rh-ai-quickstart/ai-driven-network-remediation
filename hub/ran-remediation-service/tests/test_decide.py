"""Unit tests for selecting one remediation template per Mantis RCA label."""

import pytest
from ran_remediation_service.models import RemediationState
from ran_remediation_service.nodes.decide import (
    _CATEGORY_TO_TEMPLATE,
    _FALLBACK_TEMPLATE,
    decide_node,
)


@pytest.mark.parametrize(("category", "expected_template"), _CATEGORY_TO_TEMPLATE.items())
def test_predicted_label_selects_its_own_template(category, expected_template):
    result = decide_node(RemediationState(root_cause_category=category))
    assert result["template_name"] == expected_template
    assert result["extra_vars"]["root_cause_category"] == category


def test_unmapped_category_uses_generic_template():
    result = decide_node(RemediationState(root_cause_category="new category"))
    assert result["template_name"] == _FALLBACK_TEMPLATE


def test_low_confidence_prediction_still_selects_its_template():
    enriched = {
        "incident_id": "interference-001",
        "root_cause_category": "Co-Channel Interference (Severe)",
        "ml_confidence": 0.001,
    }
    result = decide_node(RemediationState.model_validate(enriched))
    assert result["template_name"] == "ran-severe-interference-mitigation"


def test_decide_node_returns_incident_extra_vars():
    state = RemediationState(
        incident_id="abc123",
        zone="B",
        application="Twitch",
        ad_label="anomalous",
        ad_confidence=0.98,
        root_cause_category="Antenna Failure",
        root_cause="cell outage",
        recommended_fix="recover cell",
    )
    result = decide_node(state)
    assert result["template_name"] == "ran-cell-recovery"
    assert result["extra_vars"]["incident_id"] == "abc123"


def test_category_templates_are_unique():
    assert len(_CATEGORY_TO_TEMPLATE.values()) == len(set(_CATEGORY_TO_TEMPLATE.values()))
