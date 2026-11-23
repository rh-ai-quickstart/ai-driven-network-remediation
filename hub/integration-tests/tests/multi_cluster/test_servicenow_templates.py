"""Check the ServiceNow deployment contract through Helm and Make."""

import base64
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI not available")


def _make(*args):
    result = subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "TMPDIR": tempfile.gettempdir()},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _render(profile, entry_point, *overrides, namespace="hub-lab", make_vars=(), upgrading=False, error=None):
    network, telco = profile
    overrides = ("--set", "telco.ranAnomalyDetector.enabled=false", *overrides)
    if entry_point == "make":
        output = _make(
            "-n",
            "helm-install",
            f"ENABLE_NETWORK_REMEDIATION={network}",
            f"ENABLE_TELCO_ORAN={telco}",
            f"NAMESPACE={namespace}",
            "RELEASE=hub",
            "SPOKES_GENERATED=/dev/null",
            "ROUTES_ENABLED=false",
            "ENABLE_AAP_MOCK=true",
            "ENABLE_LANGFUSE=false",
            "ENABLE_LIGHTSPEED=false",
            "REGISTRY=example.test/demo",
            "VERSION=test",
            *make_vars,
            f"HELM_EXTRA_ARGS={shlex.join(overrides)}",
        )
        (upgrade,) = [
            line for line in output.replace("\\\n", "").splitlines() if line.startswith("helm upgrade --install hub hub/helm ")
        ]
        args = shlex.split(upgrade)[5:]
    else:
        args = ["--set", f"network.enabled={network},telco.enabled={telco}", *overrides]
    if upgrading:
        args.append("--is-upgrade")
    result = subprocess.run(
        ["helm", "template", "hub", "hub/helm", "-n", namespace, *args],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if error:
        assert result.returncode != 0
        assert error in result.stderr
        return []
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _resource(docs, kind, name):
    (resource,) = [doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name]
    return resource


def _container(docs, deployment):
    return _resource(docs, "Deployment", deployment)["spec"]["template"]["spec"]["containers"][0]


def _toolgroup(docs):
    config = _resource(docs, "ConfigMap", "run-config")
    groups = yaml.safe_load(config["data"]["config.yaml"])["registered_resources"]["tool_groups"]
    (group,) = [group for group in groups if group["toolgroup_id"] == "mcp::noc-servicenow"]
    assert group["mcp_endpoint"]["uri"] == "http://mcp-noc-servicenow:8000/mcp"


@pytest.mark.parametrize("profile", [("true", "false"), ("false", "true"), ("true", "true")])
@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("namespace", ["hub", "hub-lab"])
def test_each_profile_gets_one_namespace_local_servicenow(profile, entry_point, namespace):
    docs = _render(profile, entry_point, namespace=namespace)
    for name in ("mcp-noc-servicenow", "servicenow-mock"):
        for kind in ("Deployment", "Service"):
            assert _resource(docs, kind, name)["metadata"]["namespace"] == namespace
    mcp = _container(docs, "mcp-noc-servicenow")
    mock = _container(docs, "servicenow-mock")
    if entry_point == "make":
        assert mcp["image"] == "example.test/demo/noc-mcp-servicenow:test"
        assert mock["image"] == "example.test/demo/noc-servicenow-mock:test"
    env = {entry["name"]: entry for entry in mcp["env"]}
    assert env["SERVICENOW_URL"]["value"] == "http://servicenow-mock:8080"
    for container in (mcp, mock):
        credentials = [env["valueFrom"]["secretKeyRef"] for env in container["env"] if "valueFrom" in env]
        assert {ref["name"] for ref in credentials} == {"noc-servicenow-secret"}
    secret = _resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]) == b"admin"
    _toolgroup(docs)


def _env(docs, deployment):
    return {entry["name"]: entry for entry in _container(docs, deployment)["env"]}


@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("shared_url", [None, "https://shared.test"])
def test_upgrade_preserves_omitted_network_overrides_and_resource_identities(entry_point, shared_url):
    shared_settings = ("--set-string", f"global.servicenow.url={shared_url}") if shared_url else ()
    docs = _render(
        ("true", "false"),
        entry_point,
        "--set",
        "network.servicenowMock.enabled=false",
        "--set",
        "network.mcp-servers.mcp-servers.noc-servicenow.enabled=true",
        "--set-string",
        "network.mcp-servers.mcp-servers.noc-servicenow.image.repository=legacy.test/servicenow",
        "--set-string",
        "network.mcp-servers.mcp-servers.noc-servicenow.image.tag=legacy",
        "--set-string",
        "network.mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=https://legacy.test",
        "--set-string",
        "network.mcpSecrets.servicenow.apiKey=legacy-key",
        *shared_settings,
        upgrading=True,
    )
    assert all(doc["metadata"]["name"] != "servicenow-mock" for doc in docs)
    deployment = _resource(docs, "Deployment", "mcp-noc-servicenow")
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "app.kubernetes.io/name": "noc-servicenow",
        "app.kubernetes.io/instance": "hub",
    }
    assert _container(docs, "mcp-noc-servicenow")["image"] == "legacy.test/servicenow:legacy"
    for name in ("mcp-noc-servicenow", "hub-chatbot-service"):
        env = _env(docs, name)
        assert env["SERVICENOW_URL"]["value"] == (shared_url or "https://legacy.test")
        assert env["SERVICENOW_API_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "noc-servicenow-secret"
    assert "noc-servicenow" in _env(docs, "hub-agent-service")["ENABLED_MCP_SERVERS"]["value"].split(",")
    secret = _resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_API_KEY"]) == b"legacy-key"
    _toolgroup(docs)


def test_shared_values_win_over_legacy_settings():
    docs = _render(
        ("true", "true"),
        "helm",
        "--set",
        "network.servicenowMock.enabled=true,global.servicenow.mock.enabled=false",
        "--set-string",
        "network.mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=https://legacy.test",
        "--set-string",
        "network.chatbotService.env.servicenowUrl=https://legacy.test",
        "--set-string",
        "network.agentService.servicenowInstanceUrl=https://tickets.test",
        "--set-string",
        "network.mcpSecrets.servicenow.existingSecretName=legacy-servicenow",
        "--set-string",
        "global.servicenow.url=https://shared.test",
        "--set-string",
        "global.servicenow.credentials.existingSecretName=shared-servicenow",
        "--set-string",
        "global.servicenow.mcp.image.tag=shared",
    )
    assert all(doc["metadata"]["name"] not in {"servicenow-mock", "noc-servicenow-secret"} for doc in docs)
    assert _container(docs, "mcp-noc-servicenow")["image"] == "quay.io/rh-ai-quickstart/noc-mcp-servicenow:shared"
    for name in ("mcp-noc-servicenow", "hub-chatbot-service"):
        env = _env(docs, name)
        assert env["SERVICENOW_URL"]["value"] == "https://shared.test"
        for key in ("SERVICENOW_USERNAME", "SERVICENOW_PASSWORD", "SERVICENOW_API_KEY"):
            assert env[key]["valueFrom"]["secretKeyRef"] == {"name": "shared-servicenow", "key": key, "optional": True}
    assert _env(docs, "hub-agent-service")["SERVICENOW_INSTANCE_URL"]["value"] == "https://tickets.test"
    _toolgroup(docs)


def test_shared_api_key_rotation_rolls_both_clients_without_changing_the_url():
    legacy_settings = (
        "--set",
        "network.servicenowMock.enabled=false",
        "--set-string",
        "network.mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=https://servicenow.test",
        "--set-string",
        "network.mcpSecrets.servicenow.apiKey=legacy-key",
    )
    profile = ("true", "false")
    before = _render(profile, "helm", *legacy_settings, upgrading=True)
    after = _render(
        profile,
        "helm",
        *legacy_settings,
        "--set-string",
        "global.servicenow.credentials.apiKey=shared-key",
        upgrading=True,
    )
    secret = _resource(after, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_API_KEY"]) == b"shared-key"
    for deployment in ("mcp-noc-servicenow", "hub-chatbot-service"):
        assert _env(before, deployment) == _env(after, deployment)
        before_pod = _resource(before, "Deployment", deployment)["spec"]["template"]
        after_pod = _resource(after, "Deployment", deployment)["spec"]["template"]
        assert before_pod != after_pod


def test_mock_defaults_resolve_values_from_a_legacy_release():
    docs = _render(
        ("true", "false"),
        "helm",
        "--set-string",
        "network.mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=http://servicenow-mock.hub.svc:8080",
        "--set-string",
        "network.mcpSecrets.servicenow.username=",
        "--set-string",
        "network.mcpSecrets.servicenow.password=",
        upgrading=True,
    )
    assert _env(docs, "mcp-noc-servicenow")["SERVICENOW_URL"]["value"] == "http://servicenow-mock:8080"
    secret = _resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]) == b"admin"
    _toolgroup(docs)


def test_shared_basic_auth_replaces_the_legacy_mock_api_key():
    docs = _render(
        ("true", "false"),
        "helm",
        "--set",
        "global.servicenow.mock.enabled=false",
        "--set-string",
        "global.servicenow.url=https://shared.test",
        "--set-string",
        "global.servicenow.credentials.username=shared-user",
        "--set-string",
        "global.servicenow.credentials.password=shared-password",
        "--set-string",
        "network.mcpSecrets.servicenow.apiKey=demo-api-key-2026",
        upgrading=True,
    )
    secret = _resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]) == b"shared-user"
    assert base64.b64decode(secret["data"]["SERVICENOW_PASSWORD"]) == b"shared-password"
    assert secret["data"]["SERVICENOW_API_KEY"] == ""


@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize(
    ("has_url", "error"),
    [
        (False, "Set global.servicenow.url"),
        (True, "Set global.servicenow.credentials"),
    ],
)
def test_real_servicenow_requires_a_backend_and_credentials(entry_point, has_url, error):
    make_vars = ["ENABLE_SERVICENOW_MOCK=false"]
    overrides = ["--set", "global.servicenow.mock.enabled=false"] if entry_point == "helm" else []
    if has_url:
        make_vars += ["SERVICENOW_INSTANCE_URL=https://example.test", "SERVICENOW_USERNAME=test-user"]
        if entry_point == "helm":
            overrides += [
                "--set-string",
                "global.servicenow.url=https://example.test",
                "--set-string",
                "global.servicenow.credentials.username=test-user",
            ]
    _render(("false", "true"), entry_point, *overrides, make_vars=make_vars, error=error)


@pytest.mark.parametrize("profile", [("true", "false"), ("false", "true"), ("true", "true")])
@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize(
    ("make_credentials", "helm_credentials", "expected_data"),
    [
        pytest.param(
            ("SERVICENOW_API_KEY=example-key",),
            ("apiKey=example-key",),
            {"SERVICENOW_API_KEY": "example-key", "SERVICENOW_USERNAME": "", "SERVICENOW_PASSWORD": ""},
            id="api-key",
        ),
        pytest.param(
            ("SERVICENOW_USERNAME=example-user", "SERVICENOW_PASSWORD=example-password"),
            ("username=example-user", "password=example-password"),
            {
                "SERVICENOW_API_KEY": "",
                "SERVICENOW_USERNAME": "example-user",
                "SERVICENOW_PASSWORD": "example-password",
            },
            id="basic-auth",
        ),
        pytest.param(
            ("SERVICENOW_SECRET_NAME=external-servicenow",),
            ("existingSecretName=external-servicenow",),
            None,
            id="existing-secret",
        ),
    ],
)
def test_each_profile_can_connect_to_real_servicenow(
    profile, entry_point, make_credentials, helm_credentials, expected_data
):
    overrides = []
    if entry_point == "helm":
        overrides = [
            "--set",
            "global.servicenow.mock.enabled=false",
            "--set-string",
            "global.servicenow.url=https://external.test/",
        ]
        for credential in helm_credentials:
            overrides += ["--set-string", f"global.servicenow.credentials.{credential}"]
    docs = _render(
        profile,
        entry_point,
        *overrides,
        make_vars=(
            "ENABLE_SERVICENOW_MOCK=false",
            "SERVICENOW_INSTANCE_URL=https://external.test/",
            *make_credentials,
        ),
    )
    assert all(doc["metadata"]["name"] != "servicenow-mock" for doc in docs)
    if expected_data is None:
        assert all(doc["metadata"]["name"] != "noc-servicenow-secret" for doc in docs)
        secret_name = "external-servicenow"
    else:
        secret_name = "noc-servicenow-secret"
        secret = _resource(docs, "Secret", secret_name)
        assert {key: base64.b64decode(value).decode() for key, value in secret["data"].items()} == expected_data

    deployments = ["mcp-noc-servicenow"]
    if profile[0] == "true":
        deployments.append("hub-chatbot-service")
        agent_env = _env(docs, "hub-agent-service")
        assert agent_env["SERVICENOW_INSTANCE_URL"]["value"] == "https://external.test"
        assert "noc-servicenow" in agent_env["ENABLED_MCP_SERVERS"]["value"].split(",")
    for name in deployments:
        env = _env(docs, name)
        assert env["SERVICENOW_URL"]["value"] == "https://external.test"
        for key in ("SERVICENOW_USERNAME", "SERVICENOW_PASSWORD", "SERVICENOW_API_KEY"):
            assert env[key]["valueFrom"]["secretKeyRef"] == {"name": secret_name, "key": key, "optional": True}
    _toolgroup(docs)


def test_make_real_servicenow_settings_override_legacy_network_values():
    password = r"{pass,$word\' spaced}"
    docs = _render(
        ("true", "true"),
        "make",
        "--set",
        "network.servicenowMock.enabled=true",
        "--set-string",
        "network.mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=https://legacy.test",
        "--set-string",
        "network.chatbotService.env.servicenowUrl=https://legacy-chatbot.test",
        "--set-string",
        "network.mcpSecrets.servicenow.apiKey=legacy-key",
        make_vars=(
            "ENABLE_SERVICENOW_MOCK=false",
            "SERVICENOW_INSTANCE_URL=https://shared.test",
            "SERVICENOW_USERNAME=shared-user",
            f"SERVICENOW_PASSWORD={password}",
        ),
        upgrading=True,
    )
    assert all(doc["metadata"]["name"] != "servicenow-mock" for doc in docs)
    for name in ("mcp-noc-servicenow", "hub-chatbot-service"):
        assert _env(docs, name)["SERVICENOW_URL"]["value"] == "https://shared.test"
    secret = _resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]) == b"shared-user"
    assert base64.b64decode(secret["data"]["SERVICENOW_PASSWORD"]).decode() == password
    assert secret["data"]["SERVICENOW_API_KEY"] == ""
    _toolgroup(docs)


@pytest.mark.parametrize("mock_enabled", ["true", "false"])
def test_make_build_and_push_include_servicenow_in_telco_only(mock_enabled):
    args = (
        "ENABLE_NETWORK_REMEDIATION=false",
        "ENABLE_TELCO_ORAN=true",
        "ENABLE_AAP_MOCK=true",
        "ENABLE_LIGHTSPEED=false",
        f"ENABLE_SERVICENOW_MOCK={mock_enabled}",
        "REGISTRY=example.test/demo",
        "VERSION=test",
    )
    build = _make("-n", "build-all-images", *args)
    push = _make("-n", "push-all-images", *args)
    for command in (build, push):
        assert command.count("example.test/demo/noc-mcp-servicenow:test") == 1
        assert ("example.test/demo/noc-servicenow-mock:test" in command) == (mock_enabled == "true")
        assert "noc-mcp-openshift" not in command
