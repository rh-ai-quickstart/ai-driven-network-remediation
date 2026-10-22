"""Opt-in parity check for the recovered issue-196 RCA checkpoint."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import torch
from fastapi.testclient import TestClient
from ran_ml_service.model import MantisPredictor, RCA_CLASSES
from ran_ml_service.server import app

ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = ROOT / "hub/telco-oran/src/telco_oran/fixtures"
CHECKPOINT_SHA256 = "0ecd02d50eed50130476c0c543503f9f3d1595507502664d3e5b8fb01c3eccfe"
PREPROCESSING_SHA256 = "1a8e0144bb4a65f48133c4ad36491aba6432b817d64b8d3521a3c8de913ddeae"
REFERENCE = json.loads((Path(__file__).parent / "fixtures/rca_reference.json").read_text())
# The hub fixture generator provides the canonical TelecomTS samples.


@pytest.mark.skipif(not os.getenv("RCA_CHECKPOINT_PATH"), reason="RCA_CHECKPOINT_PATH is required")
def test_recovered_checkpoint_classifies_representative_real_fixtures():
    checkpoint = Path(os.environ["RCA_CHECKPOINT_PATH"])
    preprocessing = checkpoint.with_name("rca_preprocessing.json")
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == CHECKPOINT_SHA256
    assert hashlib.sha256(preprocessing.read_bytes()).hexdigest() == PREPROCESSING_SHA256
    assert REFERENCE["checkpoint_sha256"] == CHECKPOINT_SHA256
    assert REFERENCE["preprocessing_sha256"] == PREPROCESSING_SHA256
    assert REFERENCE["classes"] == list(RCA_CLASSES)
    assert hashlib.sha256((ROOT / "model-serving/shared-ml-predictor/notebooks/telecomts_model_evaluation.ipynb").read_bytes()).hexdigest() == REFERENCE["notebook_sha256"]

    predictor = MantisPredictor()
    with (
        patch("ran_ml_service.model.MANTIS_MODEL_PATH", str(checkpoint)),
        patch("ran_ml_service.model.TASK", "classify"),
        patch("ran_ml_service.server.TASK", "classify"),
        patch("ran_ml_service.server.predictor", predictor),
        TestClient(app) as client,
    ):
        assert client.get("/ready").json() == {"ready": True, "task": "classify"}
        for case in REFERENCE["cases"]:
            fixture_path = FIXTURES_DIR / case["fixture"]
            assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == case["fixture_sha256"]
            fixture = json.loads(fixture_path.read_text())
            response = client.post("/v1/classify", json={"kpi_window": fixture["kpi_window"]})
            assert response.status_code == 200
            assert response.json() == {
                "class": case["class"],
                "class_index": case["class_index"],
                "confidence": pytest.approx(case["confidence"], abs=REFERENCE["tolerances"]["confidence_absolute"]),
            }
            with torch.no_grad():
                logits = predictor.head(predictor.encoder(predictor.preprocess(fixture["kpi_window"])))[0]
            assert logits.tolist() == pytest.approx(case["logits"], abs=REFERENCE["tolerances"]["logits_absolute"])
