"""RCA classifier settings render on the RCA Deployment, including LLM-only defaults."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("url,token", [
    ("", ""),
    ("https://rca.example/v1/classify", "example-token"),
])
def test_classifier_env_on_rca_deployment(url, token):
    if shutil.which("helm") is None:
        pytest.skip("helm is required")
    result = subprocess.run(
        [
            "helm", "template", "test", str(ROOT / "hub/helm"),
            "--set", "telco.ranAnomalyDetector.enabled=false",
            "--set-string", f"telco.ranRcaService.env.classifyInferenceUrl={url}",
            "--set-string", f"telco.ranRcaService.env.classifyToken={token}",
        ],
        capture_output=True, text=True, check=True,
    )
    deployment = next(
        block for block in result.stdout.split("---")
        if "kind: Deployment" in block and "app.kubernetes.io/component: ran-rca-service" in block
    )
    assert re.search(rf'- name: CLASSIFY_INFERENCE_URL\s+value: "{re.escape(url)}"', deployment)
    assert re.search(rf'- name: CLASSIFY_TOKEN\s+value: "{re.escape(token)}"', deployment)
