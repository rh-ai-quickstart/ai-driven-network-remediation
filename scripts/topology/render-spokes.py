#!/usr/bin/env python3
"""Render spoke list for ADNR multi-cluster topology.

CLUSTER_COUNT=1 → no spokes (single-cluster dev).
CLUSTER_COUNT=N (N>=2) → N spokes named edge-site-01 .. edge-site-NN.

Writes a Helm values overlay with topology.spokes populated.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
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
deployment_mode_for = _lib.deployment_mode_for
render_values_yaml = _lib.render_values_yaml
spoke_count_for = _lib.spoke_count_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        "-o",
        default="hub/helm/spokes.generated.yaml",
        help="Path for generated Helm values overlay",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print YAML to stdout instead of writing only to a file",
    )
    args = parser.parse_args(argv)

    raw_count = (os.environ.get("CLUSTER_COUNT", "1") or "1").strip()
    try:
        cluster_count = int(raw_count)
    except ValueError:
        print("ERROR: CLUSTER_COUNT must be an integer >= 1", file=sys.stderr)
        return 1

    prefix = os.environ.get("SPOKE_NAME_PREFIX", "edge-site")
    namespace = os.environ.get("EDGE_NAMESPACE", "dark-noc-edge")
    mcp_sa = os.environ.get(
        "MCP_OPENSHIFT_SA",
        os.environ.get("MULTI_CLUSTER_CREDS_SA", "mcp-noc-openshift"),
    )

    try:
        yaml_text = render_values_yaml(
            cluster_count,
            prefix=prefix,
            namespace=namespace,
            mcp_service_account=mcp_sa,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    wrote_stdout = False
    if args.stdout or args.output in ("-", ""):
        sys.stdout.write(yaml_text)
        wrote_stdout = True

    if args.output and args.output != "-":
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml_text, encoding="utf-8")
        stream = sys.stderr if wrote_stdout else sys.stdout
        print(
            f"Wrote {out} "
            f"(deploymentMode={deployment_mode_for(cluster_count)}, "
            f"spokeCount={spoke_count_for(cluster_count)})",
            file=stream,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
