"""Helm template tests for the RAN anomaly detector's predictor token."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
HUB_CHART = REPO_ROOT / "hub" / "helm"
_DETECT_URL_SET = "telco.ranAnomalyDetector.env.detectInferenceUrl=http://predictor:8080/v1/detect"


def _helm_available() -> bool:
    try:
        subprocess.run(["helm", "version", "--short"], check=True, capture_output=True, text=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _helm_available(), reason="helm CLI not available")


def _helm_template(*extra_sets: str) -> str:
    result = subprocess.run(
        [
            "helm",
            "template",
            "hub",
            str(HUB_CHART),
            "--show-only=charts/telco/templates/ran-anomaly-detector.yaml",
            f"--set={_DETECT_URL_SET}",
            *[f"--set-string={value}" for value in extra_sets],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_ran_anomaly_detector_renders_empty_detect_token_by_default():
    rendered = _helm_template()

    assert '- name: DETECT_TOKEN\n              value: ""' in rendered


def test_ran_anomaly_detector_renders_configured_detect_token():
    rendered = _helm_template("telco.ranAnomalyDetector.env.detectToken=predictor-token")

    assert '- name: DETECT_TOKEN\n              value: "predictor-token"' in rendered
