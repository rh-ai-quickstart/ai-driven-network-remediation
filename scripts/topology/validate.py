#!/usr/bin/env python3
"""Validate ADNR topology parameters before multi-cluster deploy.

CLUSTER_COUNT=1 → single-cluster (no GitOps/oc requirements).
CLUSTER_COUNT>=2 → hub-spoke; requires GITOPS_REPO_URL, GITOPS_REVISION,
and an oc login to the hub cluster (unless SKIP_OC_CHECK=1).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _load_topology_lib():
    path = Path(__file__).resolve().parent / "lib.py"
    spec = importlib.util.spec_from_file_location("adnr_topology_lib", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load topology lib from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_lib = _load_topology_lib()
build_spokes = _lib.build_spokes
deployment_mode_for = _lib.deployment_mode_for
spoke_count_for = _lib.spoke_count_for


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_cluster_count(raw: str) -> int:
    try:
        return int(raw or "1")
    except ValueError as exc:
        raise ValueError("CLUSTER_COUNT must be an integer >= 1") from exc


def validate_topology(
    *,
    cluster_count: int,
    gitops_repo_url: str = "",
    gitops_revision: str = "",
    edge_namespace: str = "dark-noc-edge",
    spoke_name_prefix: str = "edge-site",
    skip_oc_check: bool = False,
) -> tuple[bool, list[str], dict]:
    """Return (ok, messages, summary). messages include errors and info lines."""
    messages: list[str] = []
    try:
        mode = deployment_mode_for(cluster_count)
        spokes = build_spokes(
            cluster_count, prefix=spoke_name_prefix, namespace=edge_namespace
        )
    except ValueError as exc:
        return False, [f"ERROR: {exc}"], {}

    summary = {
        "clusterCount": cluster_count,
        "deploymentMode": mode,
        "spokeCount": spoke_count_for(cluster_count),
        "edgeNamespace": edge_namespace,
        "spokeNamePrefix": spoke_name_prefix,
        "spokes": spokes,
    }

    messages.append(f"deploymentMode={mode}")
    messages.append(f"spokeCount={summary['spokeCount']}")
    if spokes:
        names = ", ".join(s["name"] for s in spokes)
        messages.append(f"spokes={names}")
    else:
        messages.append("spokes=(none)")

    if cluster_count >= 2:
        missing = []
        if not gitops_repo_url:
            missing.append("GITOPS_REPO_URL")
        if not gitops_revision:
            missing.append("GITOPS_REVISION")
        if missing:
            messages.append(
                "ERROR: hub-spoke mode requires: " + ", ".join(missing)
            )
            return False, messages, summary

        if not skip_oc_check:
            oc_ok, oc_msg = _check_oc_hub()
            messages.append(oc_msg)
            if not oc_ok:
                return False, messages, summary

    messages.append("OK: topology validation passed")
    return True, messages, summary


def _check_oc_hub() -> tuple[bool, str]:
    if not shutil.which("oc"):
        return False, "ERROR: oc not found on PATH (required for hub-spoke)"
    try:
        result = subprocess.run(
            ["oc", "whoami"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ERROR: oc whoami failed: {exc}"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "not logged in").strip()
        return False, f"ERROR: oc not logged into hub: {err}"
    user = (result.stdout or "").strip() or "unknown"
    return True, f"oc=logged in as {user}"


def main() -> int:
    try:
        cluster_count = _parse_cluster_count(_env("CLUSTER_COUNT", "1"))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    skip_oc = _env("SKIP_OC_CHECK", "").lower() in {"1", "true", "yes"}
    ok, messages, _summary = validate_topology(
        cluster_count=cluster_count,
        gitops_repo_url=_env("GITOPS_REPO_URL"),
        gitops_revision=_env("GITOPS_REVISION"),
        edge_namespace=_env("EDGE_NAMESPACE", "dark-noc-edge"),
        spoke_name_prefix=_env("SPOKE_NAME_PREFIX", "edge-site"),
        skip_oc_check=skip_oc,
    )
    for line in messages:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
