from edge_fast_path_healer.detect import (
    deployment_memory_limit_mi,
    pod_oom_event_key,
    unsafe_limit_event_key,
)


def test_pod_oom_event_key_detects_oomkilled():
    pod = {
        "metadata": {"name": "edge-nginx-abc"},
        "status": {
            "containerStatuses": [
                {
                    "name": "nginx",
                    "lastState": {
                        "terminated": {"reason": "OOMKilled", "finishedAt": "2026-08-18T12:00:00Z"}
                    },
                }
            ]
        },
    }
    assert pod_oom_event_key(pod) == "edge-nginx-abc:nginx:2026-08-18T12:00:00Z"


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
