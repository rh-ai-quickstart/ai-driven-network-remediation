"""The public deployment command renders each independently deployable task."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMMAND = ROOT / "model-serving/shared-ml-predictor/deploy/publish-and-deploy.sh"


def render(task: str, revision: str = "", **overrides: str) -> str:
    result = subprocess.run(
        [str(COMMAND)],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "TASK": task,
            "SKIP_HF_UPLOAD": "1",
            "SKIP_BUILD": "1",
            "RENDER_ONLY": "1",
            "ARTIFACT_REVISION": revision,
            "VERSION": "test-version",
            "WEIGHTS_PATH": "/not-present/weights.pt",
            "PREPROCESSING_PATH": "/not-present/rca_preprocessing.json",
            **overrides,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_detect_defaults_still_render_independent_service_and_route():
    rendered = render("detect")
    assert "name: ran-ml-service\n" in rendered
    assert "name: ran-ml-service-external\n" in rendered
    assert "name: ran-ml-service-predictor\n" in rendered
    assert "value: detect\n" in rendered
    assert "value: hf://TomerG2/mantis-ad-telecomts\n" in rendered
    assert "value: /mnt/models/mantis_pretrained_ad.pt\n" in rendered
    assert "ARTIFACT_REVISION" not in rendered


def test_classifier_initial_and_compatible_refresh_keep_service_identity():
    initial = render("classify", "rca-196-initial")
    refreshed = render("classify", "rca-196-refresh-1")
    for rendered in (initial, refreshed):
        assert "name: ran-rca-ml-service\n" in rendered
        assert "name: ran-rca-ml-service-external\n" in rendered
        assert "name: ran-rca-ml-service-predictor\n" in rendered
        assert "value: classify\n" in rendered
        assert "value: hf://TomerG2/mantis-rca-telecomts:1ffdc117e0f0c93ba779f0b5f69323d5f1c38163\n" in rendered
        assert "value: /mnt/models/mantis_pretrained_rca.pt\n" in rendered
        assert "name: ARTIFACT_REVISION\n" in rendered
    assert "value: 'rca-196-initial'" in initial
    assert "value: 'rca-196-refresh-1'" in refreshed
    assert initial != refreshed


def test_classifier_can_target_a_separate_namespace_service_and_artifact_uri():
    rendered = render(
        "classify",
        "release-2",
        ISVC_NAMESPACE="staging-models",
        ISVC_NAME="staging-rca",
        EXTERNAL_ROUTE_NAME="staging-rca-external",
        STORAGE_URI="hf://example/compatible-rca",
        CHECKPOINT_SHA256="a" * 64,
        PREPROCESSING_SHA256="b" * 64,
    )
    assert "namespace: staging-models\n" in rendered
    assert "name: staging-rca\n" in rendered
    assert "name: staging-rca-external\n" in rendered
    assert "name: staging-rca-predictor\n" in rendered
    assert "value: hf://example/compatible-rca\n" in rendered
    assert "ai.redhat.com/rca-checkpoint-sha256: " + "a" * 64 in rendered
    assert "ai.redhat.com/rca-preprocessing-sha256: " + "b" * 64 in rendered


def test_classifier_commit_override_changes_downloaded_snapshot():
    rendered = render("classify", RCA_ARTIFACT_COMMIT="a" * 40)
    assert "value: hf://TomerG2/mantis-rca-telecomts:" + "a" * 40 + "\n" in rendered


def test_classifier_default_snapshot_matches_parity_ci():
    workflow = (ROOT / ".github/workflows/pr-ran-ml-parity.yml").read_text()
    manifest = (ROOT / "model-serving/ran-rca-ml-service/deploy/inferenceservice.yaml").read_text()
    commit = "1ffdc117e0f0c93ba779f0b5f69323d5f1c38163"
    assert f"RCA_ARTIFACT_COMMIT: {commit}" in workflow
    assert f"value: hf://TomerG2/mantis-rca-telecomts:{commit}" in manifest
    assert f"value: hf://TomerG2/mantis-rca-telecomts:{commit}" in render("classify")


def test_classifier_publish_requires_separate_pinned_deploy():
    result = subprocess.run(
        [str(COMMAND)],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "TASK": "classify", "SKIP_BUILD": "1"},
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Publish RCA artifacts with SKIP_DEPLOY=1" in result.stderr


def test_classifier_deploy_from_hugging_face_needs_no_local_artifacts():
    result = subprocess.run(
        [str(COMMAND)],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "TASK": "classify",
            "SKIP_HF_UPLOAD": "1",
            "SKIP_BUILD": "1",
            "SKIP_DEPLOY": "1",
            "VERSION": "test-version",
            "WEIGHTS_PATH": "/not-present/weights.pt",
            "PREPROCESSING_PATH": "/not-present/rca_preprocessing.json",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Deployment skipped" in result.stdout
