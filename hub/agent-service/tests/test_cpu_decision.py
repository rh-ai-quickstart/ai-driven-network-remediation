from helpers import make_rca, make_state

from agent_service.models import GraphConfig
from agent_service.nodes.decide import make_decide_node


async def test_confident_cpu_throttling_routes_to_lightspeed():
    state = make_state(root_cause_analysis=make_rca(failure_type="CPUThrottling", confidence=0.96))

    decision = await make_decide_node(GraphConfig())(state)

    assert decision["decision"] == "lightspeed"


async def test_low_confidence_cpu_throttling_escalates():
    state = make_state(root_cause_analysis=make_rca(failure_type="CPUThrottling", confidence=0.5))

    decision = await make_decide_node(GraphConfig())(state)

    assert decision["decision"] == "escalate"
