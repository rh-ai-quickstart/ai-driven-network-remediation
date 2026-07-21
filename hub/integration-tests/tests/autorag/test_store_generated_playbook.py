import time
import uuid

import pytest
from helpers import (
    attach_file_to_vector_store,
    search_vector_store,
    upload_file,
    wait_for_vector_file,
)


@pytest.mark.integration
def test_store_and_retrieve_generated_playbook(
    autorag_client,
    ingested_vector_store,
):
    """Mirrors the store_generated_playbook() code path:
    same content format, same chunking config, same file naming.
    """
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    playbook_name = f"remediate-oomkilled-nginx-{suffix}"
    playbook_yaml = (
        "- name: Fix OOMKilled pod\n"
        "  hosts: all\n"
        "  tasks:\n"
        "    - name: Increase memory limit\n"
        "      kubernetes.core.k8s:\n"
        "        state: patched\n"
    )
    failure_type = "OOMKilled"
    summary = "Container killed by OOM, memory spike at 14:32"

    content = f"Failure: {failure_type}\nSummary: {summary}\nPlaybook ({failure_type}):\n{playbook_yaml}"
    filename = f"{playbook_name}.md"

    file_id = upload_file(autorag_client, filename, content)
    vector_file_id = attach_file_to_vector_store(
        autorag_client,
        ingested_vector_store,
        file_id,
    )
    wait_for_vector_file(autorag_client, ingested_vector_store, vector_file_id)

    results = search_vector_store(
        autorag_client,
        ingested_vector_store,
        "OOMKilled pod memory limit",
    )
    assert results, "Stored playbook not found via vector search"
    top_content = results[0].get("content", [])
    assert top_content, "Search hit has no content entries"
    top_text = top_content[0].get("text", "")
    assert "OOMKilled" in top_text or "memory" in top_text.lower()
