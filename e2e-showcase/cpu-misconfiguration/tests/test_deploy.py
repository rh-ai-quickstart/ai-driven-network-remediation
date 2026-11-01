"""Mock deployment tests for the CPU demo shell wrapper."""

import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / "deploy.sh"


class DeployScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.kubeconfig_dir = self.root / "kubeconfigs"
        self.kubeconfig_dir.mkdir()
        self.command_log = self.root / "commands.log"
        self._install_mocks()

    def _install_mocks(self):
        oc_script = """#!/usr/bin/env bash
set -u
printf 'oc %s\\n' "$*" >> "$MOCK_COMMAND_LOG"
if [[ "${1:-}" == "--kubeconfig" ]]; then shift 2; fi
if [[ "${1:-}" == "get" && "${2:-}" == "secret" ]]; then
  case "$3" in
    edge-site-01-admin-kubeconfig) config="${MOCK_SITE_A_ADMIN:-}" ;;
    edge-site-01-kubeconfig) config="${MOCK_SITE_A_FALLBACK:-}" ;;
    edge-site-02-admin-kubeconfig) config="${MOCK_SITE_B_ADMIN:-}" ;;
    edge-site-02-kubeconfig) config="${MOCK_SITE_B_FALLBACK:-}" ;;
    *) exit 1 ;;
  esac
  [[ -n "$config" ]] || exit 1
  printf '%s' "$config"
  exit 0
fi
if [[ "${1:-}" == "set" || "${1:-}" == "rollout" ]]; then exit "${MOCK_RESET_EXIT:-0}"; fi
if [[ "${1:-}" == "get" && "${2:-}" == "route" ]]; then
  printf 'microservice-b.apps.example.test'
  exit 0
fi
echo "Unexpected oc arguments: $*" >&2
exit 2
"""
        helm_script = """#!/usr/bin/env bash
set -u
printf 'helm %s\\n' "$*" >> "$MOCK_COMMAND_LOG"
while (($#)); do
  if [[ "$1" == "--kubeconfig" ]]; then
    config="$2"
    printf 'helm kubeconfig=%s\\n' "$(cat "$config")" >> "$MOCK_COMMAND_LOG"
    shift 2
  else
    shift
  fi
done
"""
        self._write_executable("oc", oc_script)
        self._write_executable("helm", helm_script)

    def _write_executable(self, name, contents):
        path = self.bin_dir / name
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)

    def _run_deploy(self, action="deploy", **overrides):
        environment = os.environ.copy()
        environment.update(
            PATH=f"{self.bin_dir}{os.pathsep}{environment['PATH']}",
            TMPDIR=str(self.kubeconfig_dir),
            MOCK_COMMAND_LOG=str(self.command_log),
            REGISTRY="registry.example/team",
            VERSION="test",
            SITE_A="edge-site-01",
            SITE_B="edge-site-02",
        )
        environment.update(overrides)
        return subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), action],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    @staticmethod
    def _encoded(contents):
        return base64.b64encode(contents.encode("utf-8")).decode("ascii")

    def test_single_cluster_deploy_does_not_fetch_spoke_credentials(self):
        completed = self._run_deploy(CLUSTER_COUNT="1")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        command_log = self.command_log.read_text(encoding="utf-8")
        self.assertIn("helm upgrade --install", command_log)
        self.assertIn("--set-string siteId=edge-site-01", command_log)
        self.assertIn("--set-string microserviceA.env.microserviceBSiteId=edge-site-01", command_log)
        self.assertNotIn("get secret", command_log)
        self.assertNotIn("--kubeconfig", command_log)
        self.assertEqual(list(self.kubeconfig_dir.iterdir()), [])

    def test_multi_cluster_uses_both_supported_secret_names_and_cleans_files(self):
        completed = self._run_deploy(
            CLUSTER_COUNT="2",
            MOCK_SITE_A_FALLBACK=self._encoded("site-a-config"),
            MOCK_SITE_B_ADMIN=self._encoded("site-b-config"),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        command_log = self.command_log.read_text(encoding="utf-8")
        self.assertIn("get secret edge-site-01-admin-kubeconfig", command_log)
        self.assertIn("get secret edge-site-01-kubeconfig", command_log)
        self.assertIn("get secret edge-site-02-admin-kubeconfig", command_log)
        self.assertIn("helm kubeconfig=site-a-config", command_log)
        self.assertIn("helm kubeconfig=site-b-config", command_log)
        helm_calls = [line for line in command_log.splitlines() if line.startswith("helm upgrade --install")]
        self.assertEqual(len(helm_calls), 2)
        self.assertIn("--set client.enabled=false", helm_calls[0])
        self.assertIn("--set microserviceA.enabled=false", helm_calls[0])
        self.assertIn("--set microserviceB.enabled=true", helm_calls[0])
        self.assertIn("--set client.enabled=true", helm_calls[1])
        self.assertIn("--set microserviceA.enabled=true", helm_calls[1])
        self.assertIn("--set microserviceB.enabled=false", helm_calls[1])
        self.assertIn(
            "--set-string microserviceA.env.microserviceBUrl=https://microservice-b.apps.example.test",
            helm_calls[1],
        )
        self.assertIn("--set-string microserviceA.env.microserviceBSiteId=edge-site-02", helm_calls[1])
        self.assertEqual(list(self.kubeconfig_dir.iterdir()), [])

    def test_missing_both_secret_names_fails_and_cleans_temporary_files(self):
        completed = self._run_deploy(
            CLUSTER_COUNT="2",
            MOCK_SITE_A_ADMIN=self._encoded("site-a-config"),
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Could not read spoke kubeconfig for 'edge-site-02'", completed.stderr)
        self.assertNotIn("helm upgrade --install", self.command_log.read_text(encoding="utf-8"))
        self.assertEqual(list(self.kubeconfig_dir.iterdir()), [])

    def test_reset_single_cluster_only_restores_cpu_fault(self):
        completed = self._run_deploy(action="reset", CLUSTER_COUNT="1", REGISTRY="")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        command_log = self.command_log.read_text()
        self.assertIn("set resources deployment/microservice-b", command_log)
        self.assertIn("--requests=cpu=20m --limits=cpu=25m", command_log)
        self.assertIn("rollout status deployment/microservice-b", command_log)
        self.assertNotIn("helm", command_log)
        self.assertNotIn("get secret", command_log)

    def test_reset_multi_cluster_only_uses_site_b_and_cleans_credentials(self):
        completed = self._run_deploy(
            action="reset", CLUSTER_COUNT="2", MOCK_SITE_B_ADMIN=self._encoded("site-b-config")
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        command_log = self.command_log.read_text()
        self.assertIn("get secret edge-site-02-admin-kubeconfig", command_log)
        self.assertNotIn("edge-site-01", command_log)
        self.assertIn("--kubeconfig", command_log)
        self.assertIn("set resources deployment/microservice-b", command_log)
        self.assertEqual(list(self.kubeconfig_dir.iterdir()), [])

    def test_reset_patch_failure_stops_before_rollout(self):
        completed = self._run_deploy(action="reset", CLUSTER_COUNT="1", MOCK_RESET_EXIT="1")
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("rollout status", self.command_log.read_text())


if __name__ == "__main__":
    unittest.main()
