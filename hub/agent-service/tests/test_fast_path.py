from datetime import datetime, timedelta, timezone

from agent_service.fast_path import (
    fast_path_cooldown_active,
    should_check_fast_path,
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
    assert should_check_fast_path("DNSFailure") is False


def test_target_deployment_name_uses_configured_prefix():
    assert target_deployment_name("edge-nginx-abc123-xyz") == "edge-nginx"
