import pytest
from helpers import document_id_in_hit, search_vector_store


@pytest.mark.e2e
def test_rag_e2e_retrieval(autorag_client, ingested_vector_store, rag_e2e_cases):
    for case in rag_e2e_cases:
        results = search_vector_store(
            autorag_client,
            ingested_vector_store,
            case["question"],
        )
        assert results, f"No results for question: {case['question']}"
        top_hit = results[0]
        assert top_hit["score"] > 0, f"Zero score for question: {case['question']}"
        assert document_id_in_hit(top_hit, case["correct_answer_document_ids"]), (
            f"Expected one of {case['correct_answer_document_ids']} "
            f"in top hit metadata for question: {case['question']}"
        )
