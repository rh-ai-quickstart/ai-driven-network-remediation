import uuid

import pytest


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evaluate_endpoint_exists(ingestion_client):
    """POST /evaluate returns a structured error, not 404."""
    response = ingestion_client.post("/evaluate", json={})
    assert response.status_code != 404


@pytest.mark.integration
def test_llama_stack_eval_benchmarks_reachable(autorag_client):
    response = autorag_client.get("/v1alpha/eval/benchmarks")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Integration — input validation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evaluate_missing_data_returns_400(ingestion_client):
    response = ingestion_client.post(
        "/evaluate",
        json={"scoring_functions": ["answer_relevancy"]},
    )
    assert response.status_code == 400
    assert "'data' field is required" in response.text


@pytest.mark.integration
def test_evaluate_empty_body_returns_400(ingestion_client):
    response = ingestion_client.post("/evaluate", json={})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Integration — pre-prepared RAGAS format
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evaluate_preprepared_format(ingestion_client):
    response = ingestion_client.post(
        "/evaluate",
        json={
            "scoring_functions": ["answer_relevancy"],
            "data": [
                {
                    "user_input": "How do I check consumer group lag?",
                    "response": "Use kafka-consumer-groups.sh with the --describe flag.",
                    "retrieved_contexts": [
                        "kafka-consumer-groups.sh --describe shows consumer lag per partition."
                    ],
                    "reference": "Run kafka-consumer-groups.sh with --describe",
                }
            ],
        },
        timeout=120.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert "scores" in body
    assert "answer_relevancy" in body["scores"]

    agg = body["scores"]["answer_relevancy"]["aggregated"]
    assert "answer_relevancy" in agg
    assert 0.0 <= agg["answer_relevancy"] <= 1.0

    per_row = body["scores"]["answer_relevancy"]["per_row"]
    assert len(per_row) == 1
    assert 0.0 <= per_row[0]["score"] <= 1.0
    assert body["row_count"] == 1


@pytest.mark.integration
def test_evaluate_multiple_scoring_functions(ingestion_client):
    response = ingestion_client.post(
        "/evaluate",
        json={
            "scoring_functions": ["answer_relevancy", "faithfulness"],
            "data": [
                {
                    "user_input": "How do I restart a pod?",
                    "response": "Run oc delete pod <name> to restart it.",
                    "retrieved_contexts": [
                        "Deleting a pod causes its controller to create a replacement."
                    ],
                    "reference": "Delete the pod with oc delete pod",
                }
            ],
        },
        timeout=120.0,
    )
    assert response.status_code == 200
    scores = response.json()["scores"]
    assert "answer_relevancy" in scores
    assert "faithfulness" in scores


# ---------------------------------------------------------------------------
# Integration — Llama Stack evaluation API
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_llama_stack_register_and_delete_dataset(autorag_client):
    dataset_id = f"test-ds-{uuid.uuid4().hex[:8]}"
    reg_response = autorag_client.post(
        "/v1beta/datasets",
        json={
            "dataset_id": dataset_id,
            "purpose": "eval/messages-answer",
            "source": {
                "type": "rows",
                "rows": [
                    {
                        "user_input": "What is a pod?",
                        "response": "A pod is the smallest deployable unit.",
                        "retrieved_contexts": ["Pods run one or more containers."],
                        "reference": "A pod is the smallest deployable unit in Kubernetes.",
                    }
                ],
            },
        },
        timeout=30.0,
    )
    assert reg_response.status_code == 200

    del_response = autorag_client.delete(
        f"/v1beta/datasets/{dataset_id}",
        timeout=10.0,
    )
    assert del_response.status_code == 200


@pytest.mark.integration
def test_llama_stack_register_benchmark(autorag_client):
    benchmark_id = f"test-bench-{uuid.uuid4().hex[:8]}"
    response = autorag_client.post(
        "/v1alpha/eval/benchmarks",
        json={
            "benchmark_id": benchmark_id,
            "scoring_functions": ["ragas::answer_relevancy"],
        },
        timeout=30.0,
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# E2E — project-format evaluation against ingested vector store
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_evaluate_project_format_with_vector_store(
    ingestion_client, ingested_vector_store
):
    response = ingestion_client.post(
        "/evaluate",
        json={
            "scoring_functions": ["answer_relevancy"],
            "data": [
                {
                    "question": "How do I check consumer group lag?",
                    "correct_answers": [
                        "Run kafka-consumer-groups.sh with --describe"
                    ],
                }
            ],
        },
        timeout=180.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert "scores" in body
    assert body["row_count"] == 1

    score = body["scores"]["answer_relevancy"]["per_row"][0]["score"]
    assert 0.0 <= score <= 1.0


@pytest.mark.e2e
def test_evaluate_multiple_rows(ingestion_client, ingested_vector_store):
    response = ingestion_client.post(
        "/evaluate",
        json={
            "scoring_functions": ["answer_relevancy"],
            "data": [
                {
                    "question": "How do I check consumer group lag?",
                    "correct_answers": [
                        "Run kafka-consumer-groups.sh with --describe"
                    ],
                },
                {
                    "question": "How do I restart a deployment?",
                    "correct_answers": [
                        "oc rollout restart deployment/<name>"
                    ],
                },
            ],
        },
        timeout=180.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 2
    assert len(body["scores"]["answer_relevancy"]["per_row"]) == 2
