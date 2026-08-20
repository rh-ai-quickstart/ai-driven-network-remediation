from unittest.mock import MagicMock, patch

import httpx
import respx

from edge_fast_path_healer.watcher import WatcherSettings, scan_once


@patch("edge_fast_path_healer.watcher.client.ApiClient")
@respx.mock
def test_scan_once_posts_oom_event(mock_api_client):
    settings = WatcherSettings(
        namespace="dark-noc-edge",
        deployment="edge-nginx",
        label_selector="app=edge-nginx",
        site_id="edge-01",
        runner_url="http://edge-fast-path-runner:8080/endpoint",
        unsafe_memory_limit_mi=32,
        poll_interval_seconds=10,
    )
    core_api = MagicMock()
    apps_api = MagicMock()
    pod_dict = {
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
    dep_dict = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"resources": {"limits": {"memory": "64Mi"}}}]
                }
            }
        }
    }
    mock_api_client.return_value.sanitize_for_serialization.side_effect = [dep_dict, pod_dict]
    core_api.list_namespaced_pod.return_value = MagicMock(items=[MagicMock()])
    apps_api.read_namespaced_deployment.return_value = MagicMock()
    route = respx.post(settings.runner_url).respond(200, json={"result": "success"})
    seen: set[str] = set()
    scan_once(apps_api, core_api, settings, seen, httpx.Client())
    assert route.called
    assert len(seen) == 1
