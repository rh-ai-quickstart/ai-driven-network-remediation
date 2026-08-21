from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from edge_fast_path_healer.models import RemediationResult
from edge_fast_path_healer.runner import RunnerSettings, create_app

_SETTINGS = RunnerSettings(
    namespace="dark-noc-edge",
    deployment="edge-nginx",
    site_id="edge-01",
    memory_request="64Mi",
    memory_limit="128Mi",
    cooldown_seconds=300,
)


@patch("edge_fast_path_healer.runner.load_k8s_apps_api")
def test_healthz_does_not_load_kube(mock_load):
    client = TestClient(create_app(_SETTINGS))
    assert client.get("/healthz").status_code == 200
    mock_load.assert_not_called()


@patch("edge_fast_path_healer.runner.remediate_oom")
@patch("edge_fast_path_healer.runner.load_k8s_apps_api")
def test_endpoint_triggers_remediation(mock_api, mock_remediate):
    mock_remediate.return_value = RemediationResult(
        site_id="edge-01",
        namespace="dark-noc-edge",
        deployment="edge-nginx",
        result="success",
        timestamp=datetime.now(timezone.utc),
    )
    client = TestClient(create_app(_SETTINGS))
    resp = client.post(
        "/endpoint",
        json={
            "failure_type": "OOMKilled",
            "namespace": "dark-noc-edge",
            "deployment": "edge-nginx",
            "site_id": "edge-01",
        },
    )
    assert resp.status_code == 200
    mock_remediate.assert_called_once()
    mock_api.assert_called_once()


@patch("edge_fast_path_healer.runner.load_k8s_apps_api")
def test_endpoint_ignores_wrong_namespace(mock_load):
    client = TestClient(create_app(_SETTINGS))
    resp = client.post(
        "/endpoint",
        json={
            "failure_type": "OOMKilled",
            "namespace": "other",
            "deployment": "edge-nginx",
            "site_id": "edge-01",
        },
    )
    assert resp.status_code == 202
    mock_load.assert_not_called()
