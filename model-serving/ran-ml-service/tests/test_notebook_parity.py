"""Regression test for notebook and serving inference parity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import torch
from fastapi.testclient import TestClient

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REFERENCE_PATH = FIXTURES_DIR / "notebook_reference.json"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


@pytest.fixture(scope="module")
def notebook_reference() -> dict:
    reference = json.loads(REFERENCE_PATH.read_text())
    notebook_path = REPO_ROOT / reference["notebook"]
    assert _sha256(notebook_path) == reference["notebook_sha256"], (
        "notebook changed without refreshing parity reference values"
    )
    return reference


@pytest.fixture(scope="module")
def checkpoint_path(notebook_reference: dict) -> Path:
    configured_path = os.getenv("MANTIS_MODEL_PATH")
    if not configured_path:
        pytest.skip("MANTIS_MODEL_PATH is required for notebook parity tests")

    path = Path(configured_path)
    if not path.is_file():
        pytest.fail(f"MANTIS_MODEL_PATH does not point to a file: {path}")

    actual_sha256 = _sha256(path)
    expected_sha256 = notebook_reference["checkpoint"]["sha256"]
    assert actual_sha256 == expected_sha256, "unexpected Mantis checkpoint content"
    return path


@pytest.fixture(scope="module")
def real_client(checkpoint_path: Path):
    from ran_ml_service.model import MantisPredictor
    from ran_ml_service.server import app

    predictor = MantisPredictor()
    with (
        patch("ran_ml_service.model.MANTIS_MODEL_PATH", str(checkpoint_path)),
        patch("ran_ml_service.server.predictor", predictor),
    ):
        with TestClient(app) as client:
            assert predictor.is_ready
            yield client, predictor


def test_service_matches_notebook_reference(real_client, notebook_reference: dict):
    client, predictor = real_client

    for case in notebook_reference["cases"]:
        fixture = json.loads((FIXTURES_DIR / case["fixture"]).read_text())
        response = client.post("/v1/detect", json={"kpi_window": fixture["kpi_window"]})

        assert response.status_code == 200
        result = response.json()
        assert result["label"] == case["label"]
        assert result["class_index"] == case["class_index"]
        assert result["confidence"] == pytest.approx(case["confidence"], abs=1e-4)

        model_input = predictor.preprocess(fixture["kpi_window"])
        with torch.no_grad():
            logits = predictor.head(predictor.encoder(model_input))[0]
        assert logits.tolist() == pytest.approx(case["logits"], abs=1e-4)
