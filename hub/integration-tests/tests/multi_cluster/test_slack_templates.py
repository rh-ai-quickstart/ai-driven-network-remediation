"""Check Slack configuration through the Helm and Make install interfaces."""

import base64
import shutil

import pytest

from .hub_render import deployment_env, render_hub, resource

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI not available")

NETWORK = "hub-agent-service"
TELCO = "hub-ran-remediation-service"


@pytest.mark.parametrize(
    "profile, deployments",
    [
        (("true", "false"), (NETWORK,)),
        (("false", "true"), (TELCO,)),
        (("true", "true"), (NETWORK, TELCO)),
    ],
)
@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("enabled", [False, True])
def test_installed_profiles_follow_the_hub_slack_default(profile, deployments, entry_point, enabled):
    helm_args = ("--set", "global.slack.enabled=true") if enabled and entry_point == "helm" else ()
    make_vars = ("ENABLE_SLACK=true",) if enabled else ()

    docs = render_hub(profile, entry_point, *helm_args, make_vars=make_vars)
    expected = str(enabled).lower()
    for deployment in deployments:
        assert deployment_env(docs, deployment)["SLACK_ENABLED"]["value"] == expected


@pytest.mark.parametrize("flag", ["--set", "--set-string"])
@pytest.mark.parametrize("overridden", [None, NETWORK, TELCO])
def test_blank_helm_profile_slack_overrides_inherit_the_hub_setting(flag, overridden):
    docs = render_hub(
        ("true", "true"),
        "helm",
        "--set", "global.slack.enabled=true",
        flag, f"network.agentService.slack.enabled={'false' if overridden == NETWORK else ''}",
        flag, f"telco.ranRemediationService.env.slackEnabled={'false' if overridden == TELCO else ''}",
    )
    for deployment in (NETWORK, TELCO):
        expected = "false" if deployment == overridden else "true"
        assert deployment_env(docs, deployment)["SLACK_ENABLED"]["value"] == expected


@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("hub_enabled", ["true", "false"])
def test_profile_overrides_and_channels_are_independent(entry_point, hub_enabled):
    network_enabled, telco_enabled = ("false", "true") if hub_enabled == "true" else ("true", "false")
    helm_args = (
        "--set", f"global.slack.enabled={hub_enabled}",
        "--set", f"network.agentService.slack.enabled={network_enabled}",
        "--set", f"telco.ranRemediationService.env.slackEnabled={telco_enabled}",
        "--set-string", "network.agentService.slack.channel=#network-team",
        "--set-string", "telco.ranRemediationService.env.slackChannel=#ran-team",
    )
    docs = render_hub(
        ("true", "true"),
        entry_point,
        *(helm_args if entry_point == "helm" else ()),
        make_vars=(
            f"ENABLE_SLACK={hub_enabled}",
            f"ENABLE_NETWORK_SLACK={network_enabled}",
            f"ENABLE_TELCO_SLACK={telco_enabled}",
            "SLACK_CHANNEL=#network-team",
            "TELCO_SLACK_CHANNEL=#ran-team",
        ),
    )
    for deployment, enabled, channel in [
        (NETWORK, network_enabled, "#network-team"),
        (TELCO, telco_enabled, "#ran-team"),
    ]:
        env = deployment_env(docs, deployment)
        assert env["SLACK_ENABLED"]["value"] == enabled
        assert env["SLACK_CHANNEL"]["value"] == channel


@pytest.mark.parametrize(
    "profile, deployments",
    [
        (("true", "false"), ((NETWORK, "#network-team"),)),
        (("false", "true"), ((TELCO, "#ran-team"),)),
        (("true", "true"), ((NETWORK, "#network-team"), (TELCO, "#ran-team"))),
    ],
)
@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("enabled", ["false", "true"])
def test_installed_profiles_use_one_shared_token_without_changing_enablement(profile, deployments, entry_point, enabled):
    helm_args = (
        "--set-string", "global.slack.botToken=xoxb-shared-test",
        "--set-string", "network.agentService.slack.channel=#network-team",
        "--set-string", "telco.ranRemediationService.env.slackChannel=#ran-team",
    )
    if enabled == "true" and entry_point == "helm":
        helm_args = (*helm_args, "--set", "global.slack.enabled=true")
    docs = render_hub(
        profile,
        entry_point,
        *(helm_args if entry_point == "helm" else ()),
        make_vars=(
            "SLACK_BOT_TOKEN=xoxb-shared-test",
            "SLACK_CHANNEL=#network-team",
            "TELCO_SLACK_CHANNEL=#ran-team",
            *(("ENABLE_SLACK=true",) if enabled == "true" else ()),
        ),
    )
    for deployment, channel in deployments:
        env = deployment_env(docs, deployment)
        assert env["SLACK_ENABLED"]["value"] == enabled
        assert env["SLACK_CHANNEL"]["value"] == channel
        secret_ref = env["SLACK_BOT_TOKEN"]["valueFrom"]["secretKeyRef"]
        assert (secret_ref["name"], secret_ref["key"]) == ("hub-slack-secret", "SLACK_BOT_TOKEN")

    secret = resource(docs, "Secret", "hub-slack-secret")
    assert base64.b64decode(secret["data"]["SLACK_BOT_TOKEN"]) == b"xoxb-shared-test"
    assert all(doc["metadata"]["name"] != "noc-slack-secret" for doc in docs if doc["kind"] == "Secret")


@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("network_override", [False, True])
@pytest.mark.parametrize("telco_override", [False, True])
def test_upgrade_prefers_profile_secrets_then_shared_token(entry_point, network_override, telco_override):
    helm_args = ["--set-string", "network.agentService.slack.botToken=xoxb-legacy-network"]
    make_vars = []
    if entry_point == "helm":
        helm_args.extend(["--set-string", "global.slack.botToken=xoxb-shared-test"])
    else:
        make_vars.append("SLACK_BOT_TOKEN=xoxb-shared-test")

    for selected, helm_value, make_value in (
        (
            network_override,
            "network.agentService.slack.existingSecret=network-bot",
            "NETWORK_SLACK_SECRET_NAME=network-bot",
        ),
        (
            telco_override,
            "telco.ranRemediationService.slack.existingSecret=telco-bot",
            "TELCO_SLACK_SECRET_NAME=telco-bot",
        ),
    ):
        if selected:
            if entry_point == "helm":
                helm_args.extend(["--set-string", helm_value])
            else:
                make_vars.append(make_value)

    docs = render_hub(("true", "true"), entry_point, *helm_args, make_vars=make_vars, upgrading=True)
    for deployment, expected in (
        (NETWORK, ("network-bot" if network_override else "hub-slack-secret", "SLACK_BOT_TOKEN")),
        (TELCO, ("telco-bot", "token") if telco_override else ("hub-slack-secret", "SLACK_BOT_TOKEN")),
    ):
        env = deployment_env(docs, deployment)
        secret_ref = env["SLACK_BOT_TOKEN"]["valueFrom"]["secretKeyRef"]
        assert (secret_ref["name"], secret_ref["key"]) == expected

    if not network_override:
        secret = resource(docs, "Secret", "noc-slack-secret")
        assert base64.b64decode(secret["data"]["SLACK_BOT_TOKEN"]) == b"xoxb-legacy-network"


@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize(
    "network_credential, network_secret, chart_managed",
    [
        ("network.agentService.slack.botToken=xoxb-test", "noc-slack-secret", True),
        (None, "noc-slack-secret", False),
        ("network.agentService.slack.existingSecret=noc-slack-secret", "noc-slack-secret", False),
        ("network.agentService.slack.existingSecret=external-network-secret", "external-network-secret", False),
    ],
    ids=["chart-managed", "default-secret", "explicit-default-name", "explicit-secret"],
)
@pytest.mark.parametrize("hub_enabled", ["false", "true"])
def test_upgrade_preserves_legacy_channels_and_credentials(
    entry_point, network_credential, network_secret, chart_managed, hub_enabled
):
    helm_args = ("--set", "global.slack.enabled=true") if entry_point == "helm" and hub_enabled == "true" else ()
    make_vars = ("ENABLE_SLACK=true",) if hub_enabled == "true" else ()
    docs = render_hub(
        ("true", "true"),
        entry_point,
        *helm_args,
        "--set-string",
        "network.agentService.slack.channel=#legacy-noc",
        "--set-string",
        "telco.ranRemediationService.env.slackChannel=#legacy-ran",
        *(("--set-string", network_credential) if network_credential else ()),
        make_vars=make_vars,
        upgrading=True,
    )
    for deployment, channel, secret_name, key in [
        (NETWORK, "#legacy-noc", network_secret, "SLACK_BOT_TOKEN"),
        (TELCO, "#legacy-ran", "slack-secret", "token"),
    ]:
        env = deployment_env(docs, deployment)
        assert env["SLACK_ENABLED"]["value"] == hub_enabled
        assert env["SLACK_CHANNEL"]["value"] == channel
        secret_ref = env["SLACK_BOT_TOKEN"]["valueFrom"]["secretKeyRef"]
        assert (secret_ref["name"], secret_ref["key"]) == (secret_name, key)
        assert secret_ref.get("optional", False) == (deployment == TELCO or network_credential is None)

    if chart_managed:
        secret = resource(docs, "Secret", network_secret)
        assert base64.b64decode(secret["data"]["SLACK_BOT_TOKEN"]) == b"xoxb-test"
    else:
        assert all(doc["metadata"]["name"] != network_secret for doc in docs if doc["kind"] == "Secret")
