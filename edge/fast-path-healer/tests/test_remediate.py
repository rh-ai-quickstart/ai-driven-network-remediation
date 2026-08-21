from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from edge_fast_path_healer.remediate import (
    ANNOTATION_LAST_HEAL,
    build_restart_patch,
    cooldown_active,
    parse_memory_mi,
    remediate_oom,
)


def test_parse_memory_mi():
    assert parse_memory_mi("64Mi") == 64
    assert parse_memory_mi("1Gi") == 1024
    assert parse_memory_mi("bad") is None


def test_cooldown_active_recent_annotation():
    now = datetime.now(timezone.utc).isoformat()
    dep = {
        "metadata": {"annotations": {ANNOTATION_LAST_HEAL: now}},
    }
    assert cooldown_active(dep, cooldown_seconds=300) is True
    tpl_only = {
        "metadata": {},
        "spec": {"template": {"metadata": {"annotations": {ANNOTATION_LAST_HEAL: now}}}},
    }
    assert cooldown_active(tpl_only, cooldown_seconds=300) is True


def test_cooldown_inactive_when_missing():
    assert cooldown_active({"metadata": {}}, cooldown_seconds=300) is False


def test_build_restart_patch_includes_memory_and_restart():
    patch = build_restart_patch("64Mi", "128Mi", "2026-08-18T12:00:00Z", site_id="edge-01")
    assert patch["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"] == "128Mi"
    assert patch["metadata"]["annotations"][ANNOTATION_LAST_HEAL] == "2026-08-18T12:00:00Z"
    assert "kubectl.kubernetes.io/restartedAt" in patch["spec"]["template"]["metadata"]["annotations"]


@patch("edge_fast_path_healer.remediate.client.ApiClient")
def test_remediate_oom_skips_when_cooldown_active(mock_api_client):
    api = MagicMock()
    now = datetime.now(timezone.utc).isoformat()
    api.read_namespaced_deployment.return_value = MagicMock()
    mock_api_client.return_value.sanitize_for_serialization.return_value = {
        "metadata": {"annotations": {ANNOTATION_LAST_HEAL: now}},
        "spec": {"template": {"metadata": {"annotations": {}}}},
    }
    result = remediate_oom(
        api,
        namespace="dark-noc-edge",
        deployment="edge-nginx",
        site_id="edge-01",
        memory_request="64Mi",
        memory_limit="128Mi",
        cooldown_seconds=300,
    )
    assert result.result == "skipped"
    api.patch_namespaced_deployment.assert_not_called()
