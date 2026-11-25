"""Check the ServiceNow deployment contract through Helm and Make."""

import base64
import shutil

import pytest
import yaml

from .hub_render import deployment_container, deployment_env, make, render_hub, resource

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI not available")

NETWORK_MCP = "network.mcp-servers.mcp-servers.noc-servicenow"


def _toolgroup(docs):
    config = resource(docs, "ConfigMap", "run-config")
    groups = yaml.safe_load(config["data"]["config.yaml"])["registered_resources"]["tool_groups"]
    (group,) = [group for group in groups if group["toolgroup_id"] == "mcp::noc-servicenow"]
    assert group["mcp_endpoint"]["uri"] == "http://mcp-noc-servicenow:8000/mcp"


@pytest.mark.parametrize("profile", [("true", "false"), ("false", "true"), ("true", "true")])
@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("namespace", ["hub", "hub-lab"])
def test_each_profile_gets_one_namespace_local_servicenow(profile, entry_point, namespace):
    docs = render_hub(profile, entry_point, namespace=namespace)
    for name in ("mcp-noc-servicenow", "servicenow-mock"):
        for kind in ("Deployment", "Service"):
            assert resource(docs, kind, name)["metadata"]["namespace"] == namespace
    mcp = deployment_container(docs, "mcp-noc-servicenow")
    mock = deployment_container(docs, "servicenow-mock")
    service_port = resource(docs, "Service", "mcp-noc-servicenow")["spec"]["ports"][0]
    assert (service_port["port"], service_port["targetPort"], mcp["ports"][0]["containerPort"]) == (8000, 8000, 8000)
    if entry_point == "make":
        assert mcp["image"] == "example.test/demo/noc-mcp-servicenow:test"
        assert mock["image"] == "example.test/demo/noc-servicenow-mock:test"
    env = {entry["name"]: entry for entry in mcp["env"]}
    assert env["SERVICENOW_URL"]["value"] == "http://servicenow-mock:8080"
    for container in (mcp, mock):
        credentials = [env["valueFrom"]["secretKeyRef"] for env in container["env"] if "valueFrom" in env]
        assert {ref["name"] for ref in credentials} == {"noc-servicenow-secret"}
    secret = resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]) == b"admin"
    _toolgroup(docs)


@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("shared_url", [None, "https://shared.test"])
def test_upgrade_preserves_omitted_network_overrides_and_resource_identities(entry_point, shared_url):
    shared_settings = ("--set-string", f"global.servicenow.url={shared_url}") if shared_url else ()
    docs = render_hub(
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
    deployment = resource(docs, "Deployment", "mcp-noc-servicenow")
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "app.kubernetes.io/name": "noc-servicenow",
        "app.kubernetes.io/instance": "hub",
    }
    assert deployment_container(docs, "mcp-noc-servicenow")["image"] == "legacy.test/servicenow:legacy"
    for name in ("mcp-noc-servicenow", "hub-chatbot-service"):
        env = deployment_env(docs, name)
        assert env["SERVICENOW_URL"]["value"] == (shared_url or "https://legacy.test")
        assert env["SERVICENOW_API_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "noc-servicenow-secret"
    assert "noc-servicenow" in deployment_env(docs, "hub-agent-service")["ENABLED_MCP_SERVERS"]["value"].split(",")
    secret = resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_API_KEY"]) == b"legacy-key"
    _toolgroup(docs)


def test_shared_values_win_over_legacy_settings():
    docs = render_hub(
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
    mcp = deployment_container(docs, "mcp-noc-servicenow")
    assert mcp["image"] == "quay.io/rh-ai-quickstart/noc-mcp-servicenow:shared"
    for name in ("mcp-noc-servicenow", "hub-chatbot-service"):
        env = deployment_env(docs, name)
        assert env["SERVICENOW_URL"]["value"] == "https://shared.test"
        for key in ("SERVICENOW_USERNAME", "SERVICENOW_PASSWORD", "SERVICENOW_API_KEY"):
            assert env[key]["valueFrom"]["secretKeyRef"] == {"name": "shared-servicenow", "key": key, "optional": True}
    assert deployment_env(docs, "hub-agent-service")["SERVICENOW_INSTANCE_URL"]["value"] == "https://tickets.test"
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
    before = render_hub(profile, "helm", *legacy_settings, upgrading=True)
    after = render_hub(
        profile,
        "helm",
        *legacy_settings,
        "--set-string",
        "global.servicenow.credentials.apiKey=shared-key",
        upgrading=True,
    )
    secret = resource(after, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_API_KEY"]) == b"shared-key"
    for deployment in ("mcp-noc-servicenow", "hub-chatbot-service"):
        assert deployment_env(before, deployment) == deployment_env(after, deployment)
        before_pod = resource(before, "Deployment", deployment)["spec"]["template"]
        after_pod = resource(after, "Deployment", deployment)["spec"]["template"]
        assert before_pod != after_pod


def test_mock_defaults_resolve_values_from_a_legacy_release():
    docs = render_hub(
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
    for name in ("mcp-noc-servicenow", "hub-chatbot-service"):
        assert deployment_env(docs, name)["SERVICENOW_URL"]["value"] == "http://servicenow-mock:8080"
    secret = resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]) == b"admin"
    _toolgroup(docs)


@pytest.mark.parametrize(
    ("mock_enabled", "chatbot_url"),
    [(True, None), (False, None), (True, "http://servicenow-mock:8080")],
)
def test_fresh_install_preserves_an_explicit_cross_namespace_url(mock_enabled, chatbot_url):
    url = "http://servicenow-mock.hub.svc:8080"
    real_settings = (
        "--set", "global.servicenow.mock.enabled=false",
        "--set-string", "global.servicenow.credentials.apiKey=example-key",
    )
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set-string",
        f"{NETWORK_MCP}.env.SERVICENOW_URL={url}",
        *(() if mock_enabled else real_settings),
        *(("--set-string", f"network.chatbotService.env.servicenowUrl={chatbot_url}") if chatbot_url else ()),
        namespace="tenant-a",
    )
    assert deployment_env(docs, "mcp-noc-servicenow")["SERVICENOW_URL"]["value"] == url
    assert deployment_env(docs, "hub-chatbot-service")["SERVICENOW_URL"]["value"] == (chatbot_url or url)


def test_mcp_service_routes_to_the_configured_container_port():
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set",
        f"{NETWORK_MCP}.targetPort=8123",
        "--set-string",
        f"{NETWORK_MCP}.env.APP_PORT=8123",
    )
    mcp = deployment_container(docs, "mcp-noc-servicenow")
    service_port = resource(docs, "Service", "mcp-noc-servicenow")["spec"]["ports"][0]
    assert deployment_env(docs, "mcp-noc-servicenow")["APP_PORT"]["value"] == "8123"
    assert mcp["ports"][0]["containerPort"] == 8123
    assert (service_port["port"], service_port["targetPort"]) == (8000, 8123)
    _toolgroup(docs)


@pytest.mark.parametrize("api_key", ["null", "false"])
def test_unset_shared_api_key_preserves_basic_auth(api_key):
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set",
        f"global.servicenow.mock.enabled=false,global.servicenow.credentials.apiKey={api_key}",
        "--set-string",
        "global.servicenow.url=https://instance.example.test",
        "--set-string",
        "global.servicenow.credentials.username=demo,global.servicenow.credentials.password=test",
    )
    secret = resource(docs, "Secret", "noc-servicenow-secret")
    assert tuple(
        base64.b64decode(secret["data"][key]).decode()
        for key in ("SERVICENOW_API_KEY", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD")
    ) == ("", "demo", "test")


@pytest.mark.parametrize(
    ("setting", "legacy_enabled", "expected_mock"),
    [("false", True, False), ("", True, True), ("null", True, True), ("", False, False), ("null", False, False)],
)
def test_shared_mock_switch_accepts_quoted_false_and_inherits_when_unset(setting, legacy_enabled, expected_mock):
    legacy_settings = () if legacy_enabled else ("--set", "network.servicenowMock.enabled=false")
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set" if setting == "null" else "--set-string",
        f"global.servicenow.mock.enabled={setting}",
        "--set-string",
        "global.servicenow.url=https://instance.example.test",
        "--set-string",
        "global.servicenow.credentials.username=demo,global.servicenow.credentials.password=test",
        *legacy_settings,
    )
    assert any(doc["metadata"]["name"] == "servicenow-mock" for doc in docs) == expected_mock
    if not expected_mock:
        secret = resource(docs, "Secret", "noc-servicenow-secret")
        assert base64.b64decode(secret["data"]["SERVICENOW_API_KEY"]) == b""


@pytest.mark.parametrize(
    ("shared_create", "legacy_opt_out", "managed"),
    [("null", False, True), ("false", False, False), ("null", True, False), ("true", True, True)],
)
def test_null_shared_credential_creation_inherits_the_network_setting(shared_create, legacy_opt_out, managed):
    legacy_settings = ("--set", "network.mcpSecrets.servicenow.create=false") if legacy_opt_out else ()
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set",
        f"global.servicenow.mock.enabled=false,global.servicenow.credentials.create={shared_create}",
        "--set-string",
        "global.servicenow.url=https://instance.example.test",
        "--set-string",
        "global.servicenow.credentials.apiKey=example-key",
        *legacy_settings,
    )
    secrets = [doc for doc in docs if doc["kind"] == "Secret" and doc["metadata"]["name"] == "noc-servicenow-secret"]
    assert len(secrets) == int(managed)
    if managed:
        assert base64.b64decode(secrets[0]["data"]["SERVICENOW_API_KEY"]) == b"example-key"
    for name in ("mcp-noc-servicenow", "hub-chatbot-service"):
        metadata = resource(docs, "Deployment", name)["spec"]["template"]["metadata"]
        assert ("checksum/servicenow-credentials" in metadata.get("annotations", {})) == managed


@pytest.mark.parametrize(
    ("username", "password", "shared_key", "expected_key"),
    [
        ("", "", "", "legacy-key"),
        ("shared-user", "", "", "legacy-key"),
        ("", "shared-password", "", "legacy-key"),
        ("shared-user", "shared-password", "", ""),
        ("shared-user", "shared-password", "shared-key", "shared-key"),
    ],
)
def test_shared_basic_auth_replaces_the_legacy_api_key_only_when_complete(
    username, password, shared_key, expected_key
):
    docs = render_hub(
        ("true", "false"),
        "helm",
        "--set",
        "global.servicenow.mock.enabled=false",
        "--set-string",
        "global.servicenow.url=https://shared.test",
        "--set-string",
        f"global.servicenow.credentials.username={username}",
        "--set-string",
        f"global.servicenow.credentials.password={password}",
        "--set-string",
        "network.mcpSecrets.servicenow.apiKey=legacy-key",
        *(("--set-string", f"global.servicenow.credentials.apiKey={shared_key}") if shared_key else ()),
    )
    secret = resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]).decode() == username
    assert base64.b64decode(secret["data"]["SERVICENOW_PASSWORD"]).decode() == password
    assert base64.b64decode(secret["data"]["SERVICENOW_API_KEY"]).decode() == expected_key


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
    render_hub(("false", "true"), entry_point, *overrides, make_vars=make_vars, error=error)


@pytest.mark.parametrize(
    ("shared_name", "mapping_name", "expected_name"),
    [("snow", "snow", "snow"), ("snow", "legacy", "snow"), ("", "legacy", "legacy"), ("snow", "", "snow")],
)
def test_mcp_resolves_credential_secret_names_and_keys_independently(shared_name, mapping_name, expected_name):
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set",
        "global.servicenow.mock.enabled=false,global.servicenow.credentials.create=false",
        "--set-string",
        "global.servicenow.url=https://instance.example.test",
        "--set-string",
        f"global.servicenow.credentials.existingSecretName={shared_name}",
        "--set-string",
        f"{NETWORK_MCP}.envSecrets.SERVICENOW_API_KEY.name={mapping_name}",
        "--set-string",
        f"{NETWORK_MCP}.envSecrets.SERVICENOW_API_KEY.key=api-token",
        namespace="tenant-a",
    )
    mcp = deployment_env(docs, "mcp-noc-servicenow")
    chatbot = deployment_env(docs, "hub-chatbot-service")
    default_name = shared_name or "noc-servicenow-secret"
    for name in ("SERVICENOW_API_KEY", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"):
        assert mcp[name]["valueFrom"]["secretKeyRef"] == {
            "name": expected_name if name == "SERVICENOW_API_KEY" else default_name,
            "key": "api-token" if name == "SERVICENOW_API_KEY" else name,
            "optional": True,
        }
        assert chatbot[name]["valueFrom"]["secretKeyRef"] == {"name": default_name, "key": name, "optional": True}


def test_mock_ignores_mcp_credential_mappings():
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set-string",
        f"{NETWORK_MCP}.envSecrets.SERVICENOW_API_KEY.name=snow",
    )
    for deployment, name in (("mcp-noc-servicenow", "snow"), ("servicenow-mock", "noc-servicenow-secret")):
        ref = deployment_env(docs, deployment)["SERVICENOW_API_KEY"]["valueFrom"]["secretKeyRef"]
        assert ref == {"name": name, "key": "SERVICENOW_API_KEY", "optional": True}


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
    docs = render_hub(
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
        secret = resource(docs, "Secret", secret_name)
        assert {key: base64.b64decode(value).decode() for key, value in secret["data"].items()} == expected_data

    deployments = ["mcp-noc-servicenow"]
    if profile[0] == "true":
        deployments.append("hub-chatbot-service")
        agent_env = deployment_env(docs, "hub-agent-service")
        assert agent_env["SERVICENOW_INSTANCE_URL"]["value"] == "https://external.test"
        assert "noc-servicenow" in agent_env["ENABLED_MCP_SERVERS"]["value"].split(",")
    for name in deployments:
        env = deployment_env(docs, name)
        assert env["SERVICENOW_URL"]["value"] == "https://external.test"
        for key in ("SERVICENOW_USERNAME", "SERVICENOW_PASSWORD", "SERVICENOW_API_KEY"):
            assert env[key]["valueFrom"]["secretKeyRef"] == {"name": secret_name, "key": key, "optional": True}
    _toolgroup(docs)


def test_make_real_servicenow_settings_override_legacy_network_values():
    password = r"{pass,$word\' spaced}"
    docs = render_hub(
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
        assert deployment_env(docs, name)["SERVICENOW_URL"]["value"] == "https://shared.test"
    secret = resource(docs, "Secret", "noc-servicenow-secret")
    assert base64.b64decode(secret["data"]["SERVICENOW_USERNAME"]) == b"shared-user"
    assert base64.b64decode(secret["data"]["SERVICENOW_PASSWORD"]).decode() == password
    assert secret["data"]["SERVICENOW_API_KEY"] == ""
    _toolgroup(docs)


@pytest.mark.parametrize("mock_enabled", ["true", "false", ""])
def test_make_build_and_push_include_servicenow_in_telco_only(mock_enabled):
    args = (
        "ENABLE_NETWORK_REMEDIATION=false",
        "ENABLE_TELCO_ORAN=true",
        "ENABLE_AAP_MOCK=true",
        "ENABLE_LIGHTSPEED=false",
        f"ENABLE_SERVICENOW_MOCK={mock_enabled}",
        "REGISTRY=example.test/demo",
        "VERSION=pr232",
    )
    build = make("-n", "build-all-images", *args)
    push = make("-n", "push-all-images", *args)
    mock_image = "example.test/demo/noc-servicenow-mock:pr232"
    if mock_enabled != "false":
        docs = render_hub(("false", "true"), "make", make_vars=args)
        assert deployment_container(docs, "servicenow-mock")["image"] == mock_image
    for command in (build, push):
        assert command.count("example.test/demo/noc-mcp-servicenow:pr232") == 1
        assert command.count(mock_image) == (0 if mock_enabled == "false" else 1)
        assert "noc-mcp-openshift" not in command
