import os


def test_vector_store_uses_expected_runbook_store(ingestion_client):
    response = ingestion_client.get("/vector-store")
    assert response.status_code == 200

    data = response.json()
    expected_name = os.environ.get("EXPECTED_VECTOR_STORE_NAME", "noc_runbooks")

    assert data["id"]
    assert data["name"] == expected_name
    assert data["file_counts"]["total"] >= 0
