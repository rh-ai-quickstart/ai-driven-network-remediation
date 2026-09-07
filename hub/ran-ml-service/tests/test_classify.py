"""Smoke tests for the /v1/classify endpoint with a fixture kpi_window."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ran_ml_service.server import app
from telco_oran.domain.rca_classes import RCA_CLASSES

FIXTURE_KPI_WINDOW = [[float(i + j) for j in range(18)] for i in range(128)]


class _FakePredictor:
    is_ready = True

    def predict(self, kpi_window):
        return {"class": RCA_CLASSES[0], "confidence": 0.95, "class_index": 0}


class _NotReadyPredictor:
    is_ready = False


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.predictor = _FakePredictor()
        yield c


@pytest.fixture
def unloaded_client():
    with TestClient(app) as c:
        app.state.predictor = _NotReadyPredictor()
        yield c


class TestClassifyEndpoint:
    def test_returns_classification_for_valid_input(self, client):
        resp = client.post("/v1/classify", json={"kpi_window": FIXTURE_KPI_WINDOW})
        assert resp.status_code == 200
        body = resp.json()
        assert body["class"] == "Antenna Failure"
        assert body["confidence"] == 0.95
        assert body["class_index"] == 0

    def test_returns_503_when_model_not_loaded(self, unloaded_client):
        resp = unloaded_client.post("/v1/classify", json={"kpi_window": FIXTURE_KPI_WINDOW})
        assert resp.status_code == 503
        assert "not loaded" in resp.json()["error"]

    def test_rejects_wrong_shape_kpi_window(self, client):
        resp = client.post("/v1/classify", json={"kpi_window": [[1.0] * 18] * 64})
        assert resp.status_code == 422

    def test_rejects_missing_kpi_window(self, client):
        resp = client.post("/v1/classify", json={})
        assert resp.status_code == 422


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestReadyEndpoint:
    def test_ready_when_model_loaded(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json() == {"ready": True}

    def test_not_ready_when_model_unloaded(self, unloaded_client):
        resp = unloaded_client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False
