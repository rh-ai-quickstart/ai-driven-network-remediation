import pytest
from helpers import is_sentence_transformers_model, list_models


@pytest.mark.integration
def test_health_ok(autorag_client):
    response = autorag_client.get("/v1/health")
    assert response.status_code == 200


@pytest.mark.integration
def test_models_include_sentence_transformers(autorag_client):
    models = list_models(autorag_client)
    assert any(is_sentence_transformers_model(model) for model in models)


@pytest.mark.integration
def test_embedding_api_returns_vectors(autorag_client, embedding_model_id):
    response = autorag_client.post(
        "/v1/embeddings",
        json={"model": embedding_model_id, "input": "network remediation runbook"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["data"]
    assert data["data"][0]["embedding"]
    assert len(data["data"][0]["embedding"]) > 0
