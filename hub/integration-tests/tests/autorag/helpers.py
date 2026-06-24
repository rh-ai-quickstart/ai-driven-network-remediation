import json
import time

import httpx
import pytest


def model_id(model: dict) -> str:
    return model.get("identifier") or model.get("id", "")


def is_sentence_transformers_model(model: dict) -> bool:
    provider_id = str(model.get("provider_id", ""))
    model_type = str(model.get("model_type", ""))
    identifier = model_id(model)
    return (
        "sentence-transformers" in provider_id
        or model_type == "sentence-transformers"
        or identifier.startswith("sentence-transformers/")
    )


def list_models(client: httpx.Client) -> list[dict]:
    response = client.get("/v1/models")
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        return data.get("data", data.get("models", []))
    return data


def discover_sentence_transformers_model(client: httpx.Client) -> str:
    for model in list_models(client):
        if is_sentence_transformers_model(model):
            return model_id(model)
    pytest.fail("No sentence-transformers model found in /v1/models")


def create_vector_store(client: httpx.Client, name: str, embedding_model: str) -> str:
    response = client.post(
        "/v1/vector_stores",
        json={"name": name, "embedding_model": embedding_model},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def upload_file(client: httpx.Client, filename: str, content: str) -> str:
    response = client.post(
        "/v1/files",
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
        data={"purpose": "assistants"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def attach_file_to_vector_store(
    client: httpx.Client,
    vector_store_id: str,
    file_id: str,
    attributes: dict | None = None,
) -> str:
    body: dict = {
        "file_id": file_id,
        "chunking_strategy": {
            "type": "static",
            "static": {
                "max_chunk_size_tokens": 800,
                "chunk_overlap_tokens": 80,
            },
        },
    }
    if attributes:
        body["attributes"] = attributes
    response = client.post(
        f"/v1/vector_stores/{vector_store_id}/files",
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def wait_for_vector_file(
    client: httpx.Client,
    vector_store_id: str,
    file_id: str,
    timeout: float = 60,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/v1/vector_stores/{vector_store_id}/files/{file_id}")
        assert response.status_code == 200
        if response.json().get("status") != "in_progress":
            return
        time.sleep(1)
    pytest.fail(f"Vector file {file_id} still in_progress after {timeout}s")


def search_vector_store(
    client: httpx.Client,
    vector_store_id: str,
    query: str,
    max_num_results: int = 3,
) -> list[dict]:
    response = client.post(
        f"/v1/vector_stores/{vector_store_id}/search",
        json={"query": query, "max_num_results": max_num_results},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    return data.get("data", data)


def document_id_in_hit(hit: dict, document_ids: list[str]) -> bool:
    haystack = json.dumps(hit).lower()
    return any(doc_id.lower() in haystack for doc_id in document_ids)
