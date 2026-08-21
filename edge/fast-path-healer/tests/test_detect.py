from datetime import datetime, timedelta, timezone

from edge_fast_path_healer.detect import (
    deployment_memory_limit_mi,
    pod_oom_event_key,
    unsafe_limit_event_key,
)


def _oom_pod(finished_at: str | None) -> dict:
    terminated: dict = {"reason": "OOMKilled"}
    if finished_at is not None:
        terminated["finishedAt"] = finished_at
    return {
        "metadata": {"name": "edge-nginx-abc"},
        "status": {
            "containerStatuses": [
                {
                    "name": "nginx",
                    "lastState": {"terminated": terminated},
                }
            ]
        },
    }


def test_pod_oom_event_key_detects_recent_oomkilled():
    finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert pod_oom_event_key(_oom_pod(finished)) == f"edge-nginx-abc:nginx:{finished}"


def test_pod_oom_event_key_ignores_stale_last_state():
    stale = (datetime.now(timezone.utc) - timedelta(seconds=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert pod_oom_event_key(_oom_pod(stale)) is None


def test_pod_oom_event_key_ignores_missing_finished_at():
    assert pod_oom_event_key(_oom_pod(None)) is None


def test_deployment_memory_limit_mi():
    dep = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"resources": {"limits": {"memory": "32Mi"}}}]
                }
            }
        }
    }
    assert deployment_memory_limit_mi(dep) == 32


def test_unsafe_limit_event_key_below_threshold():
    assert unsafe_limit_event_key(32, 32) == "unsafe-limit:32"
    assert unsafe_limit_event_key(64, 32) is None
