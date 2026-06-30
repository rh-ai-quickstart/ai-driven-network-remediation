import uuid

import pytest
from helpers import (
    attach_file_to_vector_store,
    create_vector_store,
    search_vector_store,
    upload_file,
    wait_for_vector_file,
)


@pytest.mark.integration
def test_vector_store_create_ingest_search(autorag_client, embedding_model_id):
    store_name = f"integration-test-{uuid.uuid4().hex[:8]}"
    vector_store_id = create_vector_store(autorag_client, store_name, embedding_model_id)

    content = "# Test Runbook\n\nRestart nginx with oc rollout restart deployment/nginx."
    file_id = upload_file(autorag_client, "test-runbook.md", content)
    vector_file_id = attach_file_to_vector_store(
        autorag_client,
        vector_store_id,
        file_id,
        attributes={"source_type": "runbook", "source_name": "test-runbook.md"},
    )
    wait_for_vector_file(autorag_client, vector_store_id, vector_file_id)

    results = search_vector_store(
        autorag_client,
        vector_store_id,
        "How do I restart nginx deployment?",
    )
    assert results
    assert results[0]["score"] > 0
