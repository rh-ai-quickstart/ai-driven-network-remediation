from unittest.mock import AsyncMock, patch

import pytest
from helpers import make_rca, make_state

from agent_service.models import GraphConfig, SelectedAction
from agent_service.nodes.decide import make_decide_node
from agent_service.nodes.match_guard import MatchVerdict


def selected_action():
    return SelectedAction(
        goal="Repair the observed resource configuration.",
        target={"edge_site_id": "edge-1", "namespace": "prod", "kind": "Deployment", "name": "worker"},
        evidence=["Configuration mismatch"],
        parameters={},
        verification=["Resource configuration matches the desired values"],
    )


@pytest.mark.parametrize("failure_type", ["CPUThrottling", "DNSFailure", "ConfigError"])
@pytest.mark.parametrize("has_selection", [False, True])
async def test_generated_remediation_requires_selection(failure_type, has_selection):
    rca = make_rca(
        failure_type=failure_type,
        recommended_actions=[],
        selected_action=selected_action() if has_selection else None,
    )
    outcome = await make_decide_node(GraphConfig())(make_state(root_cause_analysis=rca))
    assert outcome["decision"] == ("lightspeed" if has_selection else "escalate")


@pytest.mark.parametrize("has_selection", [False, True])
async def test_prebuilt_remediation_does_not_require_generated_selection(has_selection):
    rca = make_rca(
        recommended_actions=["increase memory limit"], selected_action=selected_action() if has_selection else None
    )
    with patch("agent_service.nodes.decide.verify_playbook_match", AsyncMock(return_value=MatchVerdict.MATCH)):
        outcome = await make_decide_node(GraphConfig())(make_state(root_cause_analysis=rca))
    assert outcome["decision"] == "remediate"


async def test_incomplete_generated_selection_does_not_veto_matching_prebuilt():
    selection = selected_action()
    selection = selection.model_copy(update={"target": selection.target.model_copy(update={"namespace": ""})})
    diagnosis = make_rca(
        failure_type="OOMKilled", recommended_actions=["increase memory limit"], selected_action=selection
    )
    state = make_state(root_cause_analysis=diagnosis)
    with patch(
        "agent_service.nodes.decide.verify_playbook_match", AsyncMock(return_value=MatchVerdict.MATCH)
    ) as match_guard:
        outcome = await make_decide_node(GraphConfig())(state)

    assert outcome["decision"] == "remediate"
    match_guard.assert_awaited_once()
    assert state.root_cause_analysis == diagnosis


async def test_low_confidence_still_escalates_with_selection():
    rca = make_rca(confidence=0.5, selected_action=selected_action())
    outcome = await make_decide_node(GraphConfig())(make_state(root_cause_analysis=rca))
    assert outcome["decision"] == "escalate"
