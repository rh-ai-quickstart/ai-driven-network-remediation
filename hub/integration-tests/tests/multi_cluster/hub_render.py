"""Render hub deployment manifests through Helm or the Make install command."""

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]


def make(*args):
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


def render_hub(profile, entry_point, *overrides, namespace="hub-lab", make_vars=(), upgrading=False, error=None):
    network, telco = profile
    overrides = ("--set", "telco.ranAnomalyDetector.enabled=false", *overrides)
    if entry_point == "make":
        output = make(
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
            line
            for line in output.replace("\\\n", "").splitlines()
            if line.startswith("helm upgrade --install hub hub/helm ")
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


def resource(docs, kind, name):
    (resource,) = [doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name]
    return resource


def deployment_container(docs, deployment):
    return resource(docs, "Deployment", deployment)["spec"]["template"]["spec"]["containers"][0]


def deployment_env(docs, deployment):
    return {entry["name"]: entry for entry in deployment_container(docs, deployment)["env"]}
