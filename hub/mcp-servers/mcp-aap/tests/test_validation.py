import base64
import json
import signal
import subprocess
import tempfile
from unittest.mock import Mock, patch

import httpx
import pytest
from mcp_aap import validation, validation_runner
from mcp_aap.tools import validate_playbook


def _client(responses):
    def respond(request):
        return httpx.Response(200, json=responses[request.url.path])

    return httpx.Client(base_url="https://aap.test", transport=httpx.MockTransport(respond))


def test_execution_image_uses_template_assignment():
    with _client(
        {
            "/job_templates/": {"results": [{"execution_environment": 2}]},
            "/execution_environments/2/": {"image": "registry/ee@sha256:exact"},
        }
    ) as client:
        assert validation.resolve_execution_environment(client, "base") == {
            "id": 2,
            "image": "registry/ee@sha256:exact",
        }


@pytest.mark.parametrize("relation", ["project", "organization"])
def test_execution_image_requires_explicit_template_assignment(relation):
    with _client(
        {
            "/job_templates/": {"results": [{relation: 4}]},
            f"/{relation}s/4/": {"default_environment": 2},
            "/execution_environments/2/": {"image": "registry/ee"},
        }
    ) as client:
        with pytest.raises(ValueError, match="explicit execution environment"):
            validation.resolve_execution_environment(client, "base")


def test_execution_image_does_not_guess_unexposed_controller_default():
    with _client(
        {
            "/job_templates/": {"results": [{}]},
            "/settings/all/": {"DEFAULT_EXECUTION_ENVIRONMENT": None},
        }
    ) as client:
        with pytest.raises(ValueError, match="explicit execution environment"):
            validation.resolve_execution_environment(client, "base")


def test_execution_image_requires_unique_template():
    with _client({"/job_templates/": {"results": [{}, {}]}}) as client:
        with pytest.raises(ValueError, match="unambiguous"):
            validation.resolve_execution_environment(client, "base")


def test_job_is_credential_free_and_preserves_literal_source():
    source = "- hosts: localhost\n  vars: {example: '$(TOKEN)'}\n"
    extra_vars = {"namespace": "$(UNCHANGED)"}
    job = validation.validation_job("test", "registry/ee@sha256:exact", source, extra_vars)
    pod = job["spec"]["template"]
    assert pod["metadata"]["labels"] == validation.VALIDATION_LABEL
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
    assert all("secret" not in volume for volume in pod["spec"]["volumes"])
    container = pod["spec"]["containers"][0]
    assert container["image"] == "registry/ee@sha256:exact"
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    environment = {entry["name"]: entry["value"] for entry in container["env"]}
    assert set(environment) == {"PLAYBOOK_BASE64", "EXTRA_VARS_BASE64"}
    assert base64.b64decode(environment["PLAYBOOK_BASE64"]).decode() == source
    assert json.loads(base64.b64decode(environment["EXTRA_VARS_BASE64"])) == extra_vars


@pytest.mark.parametrize("valid", [True, False])
def test_job_returns_native_verdict_and_cleans_up(valid):
    methods = []

    def respond(request):
        methods.append(request.method)
        if request.method in ("POST", "DELETE"):
            return httpx.Response(200, json={})
        if request.url.path.endswith("/pods"):
            return httpx.Response(200, json={"items": [{"metadata": {"name": "test-pod"}}]})
        if request.url.path.endswith("/log"):
            return httpx.Response(200, json={"success": True, "valid": valid, "error": "native diagnostic"})
        return httpx.Response(200, json={"status": {"succeeded": 1}})

    with httpx.Client(base_url="https://kubernetes.test", transport=httpx.MockTransport(respond)) as client:
        outcome = validation._run_job(client, "test", "image", "playbook", {})
    assert outcome["valid"] is valid
    assert methods[-1] == "DELETE"


def test_job_failure_is_infrastructure_failure_and_cleans_up():
    requests = []

    def respond(request):
        requests.append(request.method)
        return httpx.Response(200, json={"status": {"failed": 1}})

    with httpx.Client(base_url="https://kubernetes.test", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="Validation Job failed"):
            validation._run_job(client, "test", "image", "playbook", {})
    assert requests == ["POST", "GET", "DELETE"]


def test_uncertain_job_creation_is_still_cleaned_up():
    requests = []

    def respond(request):
        requests.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("Server may already have created the Job")
        return httpx.Response(200, json={})

    with httpx.Client(base_url="https://kubernetes.test", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.ReadTimeout):
            validation._run_job(client, "test", "image", "playbook", {})
    assert requests == ["POST", "DELETE"]


def test_wait_timeout_does_not_become_invalid_playbook():
    with _client({}) as client:
        with pytest.raises(TimeoutError):
            validation._await_job(client, "test", "name", deadline=0)


@pytest.mark.parametrize("extra_vars", [{"token_acm": "secret"}, {"namespace": "x" * validation.MAX_INPUT_BYTES}])
def test_rejects_credentials_and_oversized_inputs_before_cluster_access(extra_vars):
    with pytest.raises(ValueError):
        validation.validate_in_execution_environment("image", "- hosts: localhost", extra_vars)


def test_runner_scrubs_environment_and_uses_native_duplicate_key_check(tmp_path, monkeypatch):
    monkeypatch.setenv("AAP_TOKEN", "secret")
    with (
        patch.object(validation_runner.subprocess, "Popen") as start,
        patch.object(validation_runner, "_collect", return_value={"valid": True}),
    ):
        validation_runner._check(tmp_path, "duplicate: first\nduplicate: second", "{}")
    arguments = start.call_args
    assert "--syntax-check" in arguments.args[0]
    assert arguments.kwargs["env"]["ANSIBLE_DUPLICATE_YAML_DICT_KEY"] == "error"
    assert "AAP_TOKEN" not in arguments.kwargs["env"]
    assert (tmp_path / "playbook.yaml").read_text() == "duplicate: first\nduplicate: second"


def test_runner_timeout_kills_process_group():
    process = Mock(pid=123)
    process.wait.side_effect = [subprocess.TimeoutExpired("ansible", 20), -9]
    with tempfile.TemporaryFile() as output, patch.object(validation_runner.os, "killpg") as kill:
        outcome = validation_runner._collect(process, output)
    assert outcome["success"] is False
    kill.assert_called_once_with(123, signal.SIGKILL)


def test_runner_caps_native_diagnostic():
    process = Mock(pid=123)
    process.wait.return_value = 4
    with tempfile.TemporaryFile() as output, patch.object(validation_runner.os, "killpg"):
        output.write(b"x" * (validation_runner.MAX_DIAGNOSTIC_BYTES + 1))
        outcome = validation_runner._collect(process, output)
    assert outcome["success"] is True and outcome["valid"] is False
    assert outcome["diagnostic_truncated"] is True
    assert len(outcome["error"]) == validation_runner.MAX_DIAGNOSTIC_BYTES


def test_runner_signal_exit_is_infrastructure_failure():
    process = Mock(pid=123)
    process.wait.return_value = -9
    with tempfile.TemporaryFile() as output, patch.object(validation_runner.os, "killpg"):
        outcome = validation_runner._collect(process, output)
    assert outcome["success"] is False
    assert "valid" not in outcome


def test_log_budget_preserves_json_escaped_diagnostics():
    process = Mock(pid=123)
    process.wait.return_value = 4
    with tempfile.TemporaryFile() as output, patch.object(validation_runner.os, "killpg"):
        output.write(b"\x00" * validation_runner.MAX_DIAGNOSTIC_BYTES)
        outcome = validation_runner._collect(process, output)
    assert len(json.dumps(outcome).encode()) < validation.MAX_LOG_BYTES


def test_tool_reports_unavailable_validator_without_syntax_verdict():
    with (
        patch("mcp_aap.tools._aap_client"),
        patch("mcp_aap.tools.resolve_execution_environment", side_effect=ValueError("missing EE")),
    ):
        outcome = validate_playbook("- hosts: localhost", "base", {})
    assert outcome["success"] is False
    assert "valid" not in outcome
    assert "missing EE" in outcome["error"]
