from helpers import make_rca, make_state

from agent_service.models import GraphConfig, SelectedAction
from agent_service.nodes.decide import make_decide_node


async def test_confident_cpu_throttling_routes_to_lightspeed():
    selection = SelectedAction(
        goal="Increase the CPU limit of microservice-b to 250m and verify its rollout",
        target={"edge_site_id": "edge-1", "namespace": "prod", "kind": "Deployment", "name": "microservice-b"},
        evidence=["microservice-b has a CPU limit of 25m"],
        parameters={"cpu_limit": "250m"},
        verification=["The updated Deployment is available"],
    )
    state = make_state(
        root_cause_analysis=make_rca(failure_type="CPUThrottling", confidence=0.96, selected_action=selection)
    )

    decision = await make_decide_node(GraphConfig())(state)

    assert decision["decision"] == "lightspeed"


async def test_low_confidence_cpu_throttling_escalates():
    state = make_state(root_cause_analysis=make_rca(failure_type="CPUThrottling", confidence=0.5))

    decision = await make_decide_node(GraphConfig())(state)

    assert decision["decision"] == "escalate"
