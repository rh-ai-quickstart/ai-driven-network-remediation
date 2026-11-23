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


@pytest.mark.parametrize("entry_point", ["helm", "make"])
@pytest.mark.parametrize("network_secret", ["noc-slack-secret", "external-network-secret"])
@pytest.mark.parametrize("hub_enabled", ["false", "true"])
def test_upgrade_preserves_legacy_channels_and_credentials(entry_point, network_secret, hub_enabled):
    credential = (
        "network.agentService.slack.botToken=xoxb-test"
        if network_secret == "noc-slack-secret"
        else f"network.agentService.slack.existingSecret={network_secret}"
    )
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
        "--set-string",
        credential,
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

    if network_secret == "noc-slack-secret":
        secret = resource(docs, "Secret", network_secret)
        assert base64.b64decode(secret["data"]["SLACK_BOT_TOKEN"]) == b"xoxb-test"
