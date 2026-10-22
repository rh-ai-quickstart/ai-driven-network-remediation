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
            "helm", "template", "test", str(ROOT / "hub/helm/charts/telco"),
            "-f", str(ROOT / "hub/helm/values.yaml"),
            "--set", "ranAnomalyDetector.enabled=false",
            "--set-string", f"ranRcaService.env.classifyInferenceUrl={url}",
            "--set-string", f"ranRcaService.env.classifyToken={token}",
        ],
        capture_output=True, text=True, check=True,
    )
    deployment = next(
        block for block in result.stdout.split("---")
        if "kind: Deployment" in block and "app.kubernetes.io/component: ran-rca-service" in block
    )
    assert re.search(rf'- name: CLASSIFY_INFERENCE_URL\s+value: "{re.escape(url)}"', deployment)
    assert re.search(rf'- name: CLASSIFY_TOKEN\s+value: "{re.escape(token)}"', deployment)
    assert "CLASSIFY_CONFIDENCE_THRESHOLD" not in deployment
    assert "ran-rca-ml-service" not in result.stdout


def test_ci_rca_values_keep_classifier_and_consumer_images_on_helm_upgrade():
    if shutil.which("helm") is None:
        pytest.skip("helm is required")
    result = subprocess.run(
        ["helm", "template", "test", str(ROOT / "hub/helm"),
         "-f", str(ROOT / "hub/helm/values-ci-rca.yaml"),
         "--set-string", "telco.ranRcaService.image=quay.io/example/noc-ran-rca-service:commit123",
         "--set-string", "telco.ranChatbotService.image=quay.io/example/noc-ran-chatbot-service:commit123",
         "--set", "telco.ranAnomalyDetector.enabled=false"],
        capture_output=True, text=True, check=True,
    )
    rca = next(
        block for block in result.stdout.split("---")
        if "kind: Deployment" in block and "app.kubernetes.io/component: ran-rca-service" in block
    )
    chatbot = next(
        block for block in result.stdout.split("---")
        if "kind: Deployment" in block and "app.kubernetes.io/component: ran-chatbot-service" in block
    )
    assert "quay.io/example/noc-ran-rca-service:commit123" in rca
    assert "quay.io/example/noc-ran-chatbot-service:commit123" in chatbot
    assert 'value: "http://ran-rca-ml-service-predictor.model-serving.svc.cluster.local/v1/classify"' in rca
