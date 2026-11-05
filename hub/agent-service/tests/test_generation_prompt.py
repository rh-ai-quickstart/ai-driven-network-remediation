import json

import pytest
from helpers import make_rca

from agent_service.models import RemediationTarget, SelectedAction
from agent_service.nodes.lightspeed import _build_prompt


@pytest.mark.parametrize("parameter_key", ["cpu_limit", "limits.cpu"])
def test_cpu_request_preserves_target_and_parameters_without_postchecks(parameter_key):
    selected = SelectedAction(
        goal="Increase microservice-b CPU limit to 250m while preserving requests and all other settings",
        target=RemediationTarget(
            edge_site_id="edge-site-01",
            namespace="edge-ofridman",
            kind="Deployment",
            name="microservice-b",
            container="microservice-b",
        ),
        evidence=["microservice-b requests.cpu is 20m; limits.cpu is 25m"],
        parameters={parameter_key: "250m"},
        verification=[
            "Read back limits.cpu = 250m and requests.cpu = 20m for container microservice-b",
            "Confirm all other settings are unchanged and the Deployment rollout completes",
        ],
    )
    prompt = _build_prompt(make_rca(selected_action=selected), selected)

    assert selected.goal in prompt
    assert json.dumps(selected.target.model_dump()) in prompt
    assert json.dumps(selected.evidence) in prompt
    assert json.dumps(selected.parameters) in prompt
    assert all(check not in prompt for check in selected.verification)
    assert "Apply only the requested change to the exact target" in prompt
    assert "Do not add post-remediation verification" in prompt
    assert f"remediation_parameters['{parameter_key}']" in prompt
    assert "Never rename or invent parameters" in prompt
    assert "VERIFICATION\n" not in prompt


def test_non_cpu_goal_is_preserved_without_postchecks():
    selected = SelectedAction(
        goal="Increase orders memory limit to 512Mi without changing CPU limits",
        target=RemediationTarget(edge_site_id="edge-1", namespace="prod", kind="Deployment", name="orders"),
        evidence=["orders was OOMKilled at its 256Mi memory limit"],
        parameters={"memory_limit": "512Mi"},
        verification=["Read back limits.memory = 512Mi and confirm rollout completes"],
    )
    prompt = _build_prompt(make_rca(selected_action=selected), selected)

    assert selected.goal in prompt
    assert json.dumps(selected.parameters) in prompt
    assert all(check not in prompt for check in selected.verification)
    assert "Choose appropriate modules and fields for other goals" in prompt
