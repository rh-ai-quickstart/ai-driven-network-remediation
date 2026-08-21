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
)

_POD = {
    "metadata": {"name": "edge-nginx-x"},
    "status": {
        "containerStatuses": [
            {
                "name": "nginx",
                "lastState": {
                    "terminated": {
                        "reason": "OOMKilled",
                        "finishedAt": "2026-08-18T12:00:00Z",
                    }
                },
            }
        ]
    },
}

_DEP = {
    "spec": {
        "template": {
            "spec": {
                "containers": [{"resources": {"limits": {"memory": "64Mi"}}}]
            }
        }
    }
}


def _mock_k8s(mock_api_client):
    core_api = MagicMock()
    apps_api = MagicMock()
    mock_api_client.return_value.sanitize_for_serialization.side_effect = [_DEP, _POD]
    core_api.list_namespaced_pod.return_value = MagicMock(items=[MagicMock()])
    apps_api.read_namespaced_deployment.return_value = MagicMock()
    return apps_api, core_api


@patch("edge_fast_path_healer.watcher.client.ApiClient")
@respx.mock
def test_scan_once_posts_oom_event(mock_api_client):
    apps_api, core_api = _mock_k8s(mock_api_client)
    route = respx.post(_SETTINGS.runner_url).respond(200, json={"result": "success"})
    seen: set[str] = set()
    scan_once(apps_api, core_api, _SETTINGS, seen, httpx.Client())
    assert route.called
    assert len(seen) == 1


@patch("edge_fast_path_healer.watcher.client.ApiClient")
@respx.mock
def test_scan_once_does_not_mark_seen_when_post_fails(mock_api_client):
    apps_api, core_api = _mock_k8s(mock_api_client)
    route = respx.post(_SETTINGS.runner_url).respond(500, json={"result": "failed"})
    seen: set[str] = set()
    with pytest.raises(httpx.HTTPStatusError):
        scan_once(apps_api, core_api, _SETTINGS, seen, httpx.Client())
    assert route.called
    assert seen == set()
