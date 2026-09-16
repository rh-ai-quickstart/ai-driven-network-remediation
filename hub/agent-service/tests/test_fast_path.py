from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agent_service.config import FAST_PATH_LAST_HEAL_ANNOTATION
from agent_service.fast_path import (
    demo_requires_hub_aap,
    fast_path_cooldown_active,
    is_demo_oom_kafka_alert,
    should_check_fast_path,
    should_consult_spoke_fast_path,
    spoke_fast_path_actuated,
    spoke_fast_path_recent,
    target_deployment_name,
)


def test_fast_path_cooldown_active_recent():
    now = datetime.now(timezone.utc).isoformat()
    assert fast_path_cooldown_active(now, 300) is True


def test_fast_path_cooldown_inactive_when_stale():
    stale = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    assert fast_path_cooldown_active(stale, 300) is False


def test_should_check_fast_path_for_oom():
    assert should_check_fast_path("OOMKilled") is True
    assert should_check_fast_path("CrashLoopBackOff") is False
    assert should_check_fast_path("DNSFailure") is False


def test_should_check_fast_path_for_demo_oom_alert():
    raw = '{"labels":{"dark_noc_scenario":"oom","edge_site_id":"edge-01"}}'
    assert should_check_fast_path("ConfigError", raw) is True


def test_demo_requires_hub_aap_for_crashloop_and_lightspeed():
    assert demo_requires_hub_aap('{"labels":{"dark_noc_scenario":"crashloop"}}') is True
    assert demo_requires_hub_aap('{"labels":{"dark_noc_scenario":"lightspeed"}}') is True
    assert demo_requires_hub_aap('{"labels":{"dark_noc_scenario":"oom"}}') is False


def test_should_consult_spoke_fast_path_skips_crashloop_demo():
    raw = '{"labels":{"dark_noc_scenario":"crashloop"}}'
    assert should_consult_spoke_fast_path("ConfigError", raw) is False


def test_should_consult_spoke_fast_path_for_live_clf_alert():
    raw = '{"kubernetes":{"namespace_name":"dark-noc-edge","pod_name":"edge-nginx-abc-x"}}'
    assert should_consult_spoke_fast_path("ConfigError", raw) is True


def test_is_demo_oom_kafka_alert():
    assert is_demo_oom_kafka_alert('{"labels":{"dark_noc_scenario":"oom"}}') is True
    assert is_demo_oom_kafka_alert('{"labels":{"dark_noc_scenario":"crashloop"}}') is False


def test_spoke_fast_path_actuated_demo_oom_after_cooldown():
    stale = "2020-01-01T00:00:00Z"
    assert spoke_fast_path_actuated(stale, cooldown_seconds=300, demo_oom_alert=True) is True
    assert spoke_fast_path_actuated(stale, cooldown_seconds=300, demo_oom_alert=False) is False


def test_target_deployment_name_derives_from_replicaset_pod():
    assert target_deployment_name("edge-nginx-6b7f8c9d4-x2k9z") == "edge-nginx"
    assert target_deployment_name("payments-api-7d8f9c6b5-abcde") == "payments-api"


def test_target_deployment_name_maps_demo_nginx_edge_pod():
    assert (
        target_deployment_name("nginx-edge-oom", namespace="dark-noc-edge")
        == "edge-nginx"
    )


def test_target_deployment_name_returns_none_when_unparseable():
    assert target_deployment_name("other-pod") is None
    assert target_deployment_name("edge-nginx-abc123") is None
    assert target_deployment_name("edge-nginx-6b7f8c9d4-x2k") is None
    assert target_deployment_name("") is None


@pytest.mark.asyncio
async def test_spoke_fast_path_recent_true_when_annotation_fresh():
    now = datetime.now(timezone.utc).isoformat()
    with patch(
        "agent_service.fast_path.invoke_tool",
        AsyncMock(
            return_value={
                "success": True,
                "annotations": {FAST_PATH_LAST_HEAL_ANNOTATION: now},
            }
        ),
    ):
        assert await spoke_fast_path_recent(
            namespace="dark-noc-edge",
            deployment="edge-nginx",
            edge_site_id="edge-01",
            raw_event='{"labels":{"dark_noc_scenario":"oom"}}',
        )


@pytest.mark.asyncio
async def test_spoke_fast_path_recent_false_when_annotation_stale():
    stale = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    with patch(
        "agent_service.fast_path.invoke_tool",
        AsyncMock(
            return_value={
                "success": True,
                "annotations": {FAST_PATH_LAST_HEAL_ANNOTATION: stale},
            }
        ),
    ):
        assert (
            await spoke_fast_path_recent(
                namespace="dark-noc-edge",
                deployment="edge-nginx",
                edge_site_id="edge-01",
            )
            is False
        )


@pytest.mark.asyncio
async def test_spoke_fast_path_recent_true_when_stale_but_demo_oom():
    stale = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    with patch(
        "agent_service.fast_path.invoke_tool",
        AsyncMock(
            return_value={
                "success": True,
                "annotations": {FAST_PATH_LAST_HEAL_ANNOTATION: stale},
            }
        ),
    ):
        assert await spoke_fast_path_recent(
            namespace="dark-noc-edge",
            deployment="edge-nginx",
            edge_site_id="edge-01",
            raw_event='{"labels":{"dark_noc_scenario":"oom","edge_site_id":"edge-01"}}',
        )


@pytest.mark.asyncio
async def test_spoke_fast_path_recent_false_on_mcp_error_payload():
    with patch(
        "agent_service.fast_path.invoke_tool",
        AsyncMock(return_value={"success": False, "error": "not found", "annotations": {}}),
    ):
        assert (
            await spoke_fast_path_recent(
                namespace="dark-noc-edge",
                deployment="edge-nginx",
                edge_site_id="edge-01",
            )
            is False
        )


@pytest.mark.asyncio
async def test_spoke_fast_path_recent_false_when_invoke_raises():
    with patch(
        "agent_service.fast_path.invoke_tool",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    ):
        assert (
            await spoke_fast_path_recent(
                namespace="dark-noc-edge",
                deployment="edge-nginx",
                edge_site_id="edge-01",
            )
            is False
        )
