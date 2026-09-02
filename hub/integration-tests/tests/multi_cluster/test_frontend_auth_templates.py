"""Helm template tests for the global.frontendAuth.enabled oauth-proxy gate.

Covers what `--set global.frontendAuth.enabled=true` emits for hub-frontend
and hub-ran-frontend (both deployed by default -- see network.enabled /
telco.enabled subchart conditions): the oauth-proxy sidecar, the
ServiceAccount redirect-reference annotation, the Service targetPort switch,
the per-frontend cookie Secrets, the NGINX_LISTEN_ADDRESS env override that
binds nginx to loopback-only (so the pod IP can't be hit directly on 8080 to
skip OAuth), and the exec-based liveness/readiness probes that replace the
httpGet ones once nginx stops listening on the pod's routable interface -- and
confirms the default (disabled) render emits none of that, with Services
still pointed at nginx's own port and the original httpGet probes intact.

Also covers that each frontend's oauth cookie Secret is gated on that same
frontend's own subchart toggle (network.enabled / telco.enabled) in addition
to global.frontendAuth.enabled, so disabling a whole use case doesn't leave
an orphaned Secret behind for a frontend that isn't deployed.

These are static `helm template` assertions (no live cluster/OpenShift OAuth
server involved), so they catch a broken `if`/helper include immediately
rather than only on a live login attempt.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
HUB_CHART = REPO_ROOT / "hub" / "helm"


def _helm_available() -> bool:
    try:
        subprocess.run(
            ["helm", "version", "--short"],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _helm_available(), reason="helm CLI not available")


def _run_helm_template(show_only: list[str], extra_sets: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [
        "helm",
        "template",
        "hub",
        str(HUB_CHART),
        "--namespace",
        "hub",
        *[f"--show-only={t}" for t in show_only],
        *[f"--set={s}" for s in extra_sets],
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _helm_template(show_only: list[str], *extra_sets: str) -> str:
    result = _run_helm_template(show_only, list(extra_sets))
    assert result.returncode == 0, (
        f"helm template failed ({result.returncode}):\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip(), "helm template produced empty output"
    return result.stdout


def _assert_template_renders_nothing(show_only: str, extra_sets: list[str]) -> None:
    # `helm template --show-only` errors with "could not find template" when the
    # requested template's guarded content produces no output at all -- exactly
    # what we want to assert here (e.g. the oauth secret template when the gate
    # is off, or ran-frontend.yaml when the telco subchart is disabled).
    result = _run_helm_template([show_only], extra_sets)
    assert result.returncode != 0, f"expected {show_only} to render nothing, but it produced:\n{result.stdout}"
    assert "could not find template" in result.stderr


def test_frontend_auth_disabled_by_default_emits_no_oauth_resources():
    # Default values: global.frontendAuth.enabled=false; both frontends are
    # deployed by default (network.enabled / telco.enabled).
    rendered = _helm_template(["charts/network/templates/frontend.yaml", "charts/telco/templates/ran-frontend.yaml"])

    assert "kind: ServiceAccount" not in rendered
    assert "oauth-redirectreference" not in rendered
    assert "oauth-proxy" not in rendered
    assert "targetPort: 8888" not in rendered
    assert "serviceAccountName:" not in rendered
    # Both Services still route to nginx's own port.
    assert rendered.count("targetPort: 8080") == 2

    # Both nginx containers still bind all interfaces with httpGet probes.
    assert "NGINX_LISTEN_ADDRESS" not in rendered
    assert "wget" not in rendered
    assert rendered.count("httpGet:") == 4

    # The oauth cookie-secret templates must render nothing when the gate is off.
    _assert_template_renders_nothing("charts/network/templates/frontend-oauth-secret.yaml", [])
    _assert_template_renders_nothing("charts/telco/templates/ran-frontend-oauth-secret.yaml", [])


def test_frontend_auth_enabled_adds_oauth_proxy_sidecar_and_service_account():
    extra_sets = ["global.frontendAuth.enabled=true"]
    rendered = _helm_template(
        [
            "charts/network/templates/frontend.yaml",
            "charts/telco/templates/ran-frontend.yaml",
            "charts/network/templates/frontend-oauth-secret.yaml",
            "charts/telco/templates/ran-frontend-oauth-secret.yaml",
        ],
        *extra_sets,
    )

    # oauth-proxy sidecar present for both frontends.
    assert rendered.count("name: oauth-proxy") == 2
    assert rendered.count("origin-oauth-proxy") == 2
    assert "--openshift-service-account=hub-frontend" in rendered
    assert "--openshift-service-account=hub-ran-frontend" in rendered
    assert "--upstream=http://localhost:8080" in rendered

    # Each frontend gets its own ServiceAccount, annotated for OAuth self-registration.
    assert rendered.count("kind: ServiceAccount") == 2
    assert rendered.count("serviceaccounts.openshift.io/oauth-redirectreference.primary") == 2
    assert '\\"kind\\":\\"Route\\",\\"name\\":\\"hub-frontend\\"' in rendered
    assert '\\"kind\\":\\"Route\\",\\"name\\":\\"hub-ran-frontend\\"' in rendered

    # Pod spec points each Deployment at its matching ServiceAccount.
    assert "serviceAccountName: hub-frontend" in rendered
    assert "serviceAccountName: hub-ran-frontend" in rendered

    # Services now route to the sidecar's port instead of nginx's.
    assert rendered.count("targetPort: 8888") == 2
    assert "targetPort: 8080" not in rendered

    # Cookie-signing Secrets for both sidecars.
    assert "name: hub-frontend-oauth" in rendered
    assert "name: hub-ran-frontend-oauth" in rendered
    assert rendered.count("cookie-secret:") == 2

    # nginx is forced to loopback-only in both frontends, since the Service no
    # longer targets its port directly -- only the oauth-proxy sidecar
    # (sharing the pod's network namespace) should be able to reach it.
    assert rendered.count("NGINX_LISTEN_ADDRESS") == 2
    assert rendered.count('value: "127.0.0.1"') == 2

    # httpGet probes can't reach a loopback-only nginx (kubelet connects to the
    # pod's routable IP, not localhost), so both switch to exec-based probes.
    assert "httpGet:" not in rendered
    assert rendered.count("exec:") == 4
    assert rendered.count("wget") == 4


def test_frontend_auth_enabled_with_telco_oran_disabled_only_covers_frontend():
    # Disabling the whole Telco/O-RAN use case (telco.enabled=false) must take
    # ran-frontend out of the picture entirely, even though ranFrontend.enabled
    # still defaults to true inside the telco subchart.
    extra_sets = ["global.frontendAuth.enabled=true", "telco.enabled=false"]
    rendered = _helm_template(
        ["charts/network/templates/frontend.yaml", "charts/network/templates/frontend-oauth-secret.yaml"],
        *extra_sets,
    )

    assert rendered.count("name: oauth-proxy") == 1
    assert "--openshift-service-account=hub-frontend" in rendered
    assert "--openshift-service-account=hub-ran-frontend" not in rendered

    assert "name: hub-frontend-oauth" in rendered
    assert "hub-ran-frontend-oauth" not in rendered

    assert rendered.count("targetPort: 8888") == 1

    assert rendered.count("NGINX_LISTEN_ADDRESS") == 1
    assert "httpGet:" not in rendered
    assert rendered.count("exec:") == 2
    assert rendered.count("wget") == 2

    _assert_template_renders_nothing("charts/telco/templates/ran-frontend.yaml", extra_sets)


def test_frontend_auth_enabled_with_network_remediation_disabled_only_covers_ran_frontend():
    # Mirror of the telco test above, for the other frontend: disabling
    # network.enabled must take hub-frontend (and its oauth secret) out of
    # the picture, without touching hub-ran-frontend.
    extra_sets = ["global.frontendAuth.enabled=true", "network.enabled=false"]
    rendered = _helm_template(
        ["charts/telco/templates/ran-frontend.yaml", "charts/telco/templates/ran-frontend-oauth-secret.yaml"],
        *extra_sets,
    )

    assert rendered.count("name: oauth-proxy") == 1
    assert "--openshift-service-account=hub-ran-frontend" in rendered
    assert "--openshift-service-account=hub-frontend" not in rendered

    assert "name: hub-ran-frontend-oauth" in rendered
    assert "hub-frontend-oauth" not in rendered

    _assert_template_renders_nothing("charts/network/templates/frontend.yaml", extra_sets)
