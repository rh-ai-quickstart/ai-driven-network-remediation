"""Run native Ansible syntax checks in isolated, credential-free Kubernetes Jobs."""

import base64
import json
import logging
import time
import uuid
from pathlib import Path

import httpx

VALIDATION_WAIT_SECONDS = 70
VALIDATION_JOB_DEADLINE_SECONDS = 60
VALIDATION_JOB_TTL_SECONDS = 120
VALIDATION_POLL_SECONDS = 1
API_TIMEOUT_SECONDS = 5
MAX_INPUT_BYTES = 65536
# JSON escaping can expand an 8000-byte diagnostic to almost 48 KiB.
MAX_LOG_BYTES = 65536
VALIDATION_LABEL = {"app.kubernetes.io/component": "playbook-validation"}
VALIDATION_SERVICE_ACCOUNT = "mcp-aap-validator"
SERVICE_ACCOUNT_DIRECTORY = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_LAUNCH_VARIABLES = frozenset(
    {
        "edge_site_id",
        "namespace",
        "resource_kind",
        "resource_name",
        "container_name",
        "remediation_parameters",
        "deployment",
        "deployment_name",
    }
)
logger = logging.getLogger(__name__)


def _get_json(client: httpx.Client, path: str, **kwargs) -> dict:
    response = client.get(path, timeout=API_TIMEOUT_SECONDS, **kwargs)
    response.raise_for_status()
    return response.json()


def resolve_execution_environment(client: httpx.Client, template_name: str) -> dict:
    templates = _get_json(client, "/job_templates/", params={"name": template_name})["results"]
    if len(templates) != 1:
        raise ValueError("Validation requires one unambiguous AAP base template")
    template = templates[0]
    environment_id = template.get("execution_environment")
    if environment_id:
        environment = _get_json(client, f"/execution_environments/{int(environment_id)}/")
    else:
        detail = _get_json(client, f"/job_templates/{int(template['id'])}/")
        summary = detail.get("summary_fields")
        environment = summary.get("resolved_environment") if isinstance(summary, dict) else None
        environment_id = environment.get("id") if isinstance(environment, dict) else None
        if type(environment_id) is not int or environment_id <= 0:
            raise ValueError("AAP base template has no resolved execution environment")
    if not isinstance(environment.get("image"), str) or not environment["image"].strip():
        raise ValueError("AAP execution environment has no image")
    return {"id": int(environment_id), "image": environment["image"]}


def _container(image: str, playbook: str, extra_vars: dict) -> dict:
    encoded = {
        "PLAYBOOK_BASE64": base64.b64encode(playbook.encode()).decode(),
        "EXTRA_VARS_BASE64": base64.b64encode(json.dumps(extra_vars).encode()).decode(),
    }
    return {
        "name": "syntax-check",
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python3", "-c", Path(__file__).with_name("validation_runner.py").read_text()],
        "env": [{"name": name, "value": value} for name, value in encoded.items()],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "1", "memory": "512Mi", "ephemeral-storage": "128Mi"},
        },
        "volumeMounts": [{"name": "scratch", "mountPath": "/tmp"}],
    }


def validation_job(name: str, image: str, playbook: str, extra_vars: dict) -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": VALIDATION_JOB_DEADLINE_SECONDS,
            "ttlSecondsAfterFinished": VALIDATION_JOB_TTL_SECONDS,
            "template": {
                "metadata": {"labels": VALIDATION_LABEL},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "serviceAccountName": VALIDATION_SERVICE_ACCOUNT,
                    "enableServiceLinks": False,
                    "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [_container(image, playbook, extra_vars)],
                    "volumes": [{"name": "scratch", "emptyDir": {"sizeLimit": "128Mi"}}],
                },
            },
        },
    }


def _job_output(client: httpx.Client, namespace: str, name: str) -> dict:
    pods = _get_json(client, f"/api/v1/namespaces/{namespace}/pods", params={"labelSelector": f"job-name={name}"})
    if len(pods.get("items", [])) != 1:
        raise ValueError("Validation Job has no unique result pod")
    pod_name = pods["items"][0]["metadata"]["name"]
    response = client.get(
        f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log",
        params={"container": "syntax-check", "limitBytes": MAX_LOG_BYTES},
        timeout=API_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    outcome = json.loads(response.text)
    if not isinstance(outcome, dict):
        raise ValueError("Invalid validation result")
    if outcome.get("success") is not True:
        raise ValueError(outcome.get("error", "Validation runner failed"))
    if type(outcome.get("valid")) is not bool:
        raise ValueError("Validation runner did not return a verdict")
    return outcome


def _await_job(client: httpx.Client, namespace: str, name: str, deadline: float) -> dict:
    while time.monotonic() < deadline:
        status = _get_json(client, f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}").get("status", {})
        if status.get("succeeded"):
            return _job_output(client, namespace, name)
        failed = next(
            (
                condition
                for condition in status.get("conditions", [])
                if condition.get("type") == "Failed" and condition.get("status") == "True"
            ),
            None,
        )
        if failed or status.get("failed"):
            raise ValueError(f"Validation Job failed: {(failed or {}).get('reason', 'container failure')}")
        time.sleep(VALIDATION_POLL_SECONDS)
    raise TimeoutError("Validation Job did not finish within its deadline; check scheduling and image pulls")


def _run_job(client: httpx.Client, namespace: str, image: str, playbook: str, extra_vars: dict) -> dict:
    name = "playbook-validation-" + uuid.uuid4().hex
    path = f"/apis/batch/v1/namespaces/{namespace}/jobs"
    deadline = time.monotonic() + VALIDATION_WAIT_SECONDS
    try:
        response = client.post(path, json=validation_job(name, image, playbook, extra_vars))
        response.raise_for_status()
        return _await_job(client, namespace, name, deadline)
    finally:
        try:
            cleanup = client.delete(f"{path}/{name}", params={"propagationPolicy": "Background"})
            if cleanup.status_code != 404:
                cleanup.raise_for_status()
        except httpx.HTTPError:
            logger.warning("Validation Job cleanup failed; TTL cleanup remains enabled", exc_info=True)


def validate_in_execution_environment(image: str, playbook: str, extra_vars: dict) -> dict:
    if not playbook.strip() or len(playbook.encode()) + len(json.dumps(extra_vars).encode()) > MAX_INPUT_BYTES:
        raise ValueError("Validation input is empty or exceeds 64 KiB")
    if set(extra_vars) - _LAUNCH_VARIABLES:
        raise ValueError("Validation accepts only noncredential remediation launch variables")
    namespace = (SERVICE_ACCOUNT_DIRECTORY / "namespace").read_text().strip()
    token = (SERVICE_ACCOUNT_DIRECTORY / "token").read_text().strip()
    with httpx.Client(
        base_url="https://kubernetes.default.svc",
        headers={"Authorization": f"Bearer {token}"},
        verify=str(SERVICE_ACCOUNT_DIRECTORY / "ca.crt"),
        timeout=API_TIMEOUT_SECONDS,
    ) as client:
        return _run_job(client, namespace, image, playbook, extra_vars)
