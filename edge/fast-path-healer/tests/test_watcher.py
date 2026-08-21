from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from edge_fast_path_healer.watcher import WatcherSettings, scan_once

_SETTINGS = WatcherSettings(
    namespace="dark-noc-edge",
    deployment="edge-nginx",
    label_selector="app=edge-nginx",
    site_id="edge-01",
    runner_url="http://edge-fast-path-runner:8080/endpoint",
    unsafe_memory_limit_mi=32,
    poll_interval_seconds=10,
    cooldown_seconds=300,
)

_DEP = {
    "spec": {
        "template": {
            "spec": {
                "containers": [{"resources": {"limits": {"memory": "64Mi"}}}]
            }
        }
    }
}


def _oom_pod(finished_at: str) -> dict:
    return {
        "metadata": {"name": "edge-nginx-x"},
        "status": {
            "containerStatuses": [
                {
                    "name": "nginx",
                    "lastState": {
                        "terminated": {
                            "reason": "OOMKilled",
                            "finishedAt": finished_at,
                        }
                    },
                }
            ]
        },
    }


def _mock_k8s(mock_api_client, pod: dict):
    core_api = MagicMock()
    apps_api = MagicMock()
    mock_api_client.return_value.sanitize_for_serialization.side_effect = [_DEP, pod]
    core_api.list_namespaced_pod.return_value = MagicMock(items=[MagicMock()])
    apps_api.read_namespaced_deployment.return_value = MagicMock()
    return apps_api, core_api


def _recent_finished_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@patch("edge_fast_path_healer.watcher.client.ApiClient")
@respx.mock
def test_scan_once_posts_oom_event(mock_api_client):
    apps_api, core_api = _mock_k8s(mock_api_client, _oom_pod(_recent_finished_at()))
    route = respx.post(_SETTINGS.runner_url).respond(200, json={"result": "success"})
    seen: set[str] = set()
    scan_once(apps_api, core_api, _SETTINGS, seen, httpx.Client())
    assert route.called
    assert len(seen) == 1


@patch("edge_fast_path_healer.watcher.client.ApiClient")
@respx.mock
def test_scan_once_does_not_mark_seen_when_post_fails(mock_api_client):
    apps_api, core_api = _mock_k8s(mock_api_client, _oom_pod(_recent_finished_at()))
    route = respx.post(_SETTINGS.runner_url).respond(500, json={"result": "failed"})
    seen: set[str] = set()
    with pytest.raises(httpx.HTTPStatusError):
        scan_once(apps_api, core_api, _SETTINGS, seen, httpx.Client())
    assert route.called
    assert seen == set()


@patch("edge_fast_path_healer.watcher.client.ApiClient")
@respx.mock
def test_scan_once_ignores_stale_last_state(mock_api_client):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    apps_api, core_api = _mock_k8s(mock_api_client, _oom_pod(stale))
    route = respx.post(_SETTINGS.runner_url).respond(200, json={"result": "success"})
    seen: set[str] = set()
    scan_once(apps_api, core_api, _SETTINGS, seen, httpx.Client())
    assert not route.called
    assert seen == set()
