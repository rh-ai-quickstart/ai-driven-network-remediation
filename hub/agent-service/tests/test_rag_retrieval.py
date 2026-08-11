from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from helpers import make_state


def _patch_rag_client(mock):
    return patch("agent_service.nodes.rag_retrieval._rag_client", mock)


class TestRagQueryConstruction:
    @pytest.mark.asyncio
    async def test_query_built_from_log_event_fields(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            state = make_state()
            result = await rag_retrieval_node(state)

        assert "CrashLoopBackOff" in result["rag_query_used"]
        assert "namespace=prod" in result["rag_query_used"]
        assert "pod=nginx-abc123" in result["rag_query_used"]


class TestRagSuccessfulSearch:
    @pytest.mark.asyncio
    async def test_returns_snippets_from_search_results(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=["Runbook: restart the pod", "Runbook: check memory limits"])

        with _patch_rag_client(mock):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == [
            "Runbook: restart the pod",
            "Runbook: check memory limits",
        ]
        assert result["rag_query_used"] != ""


class TestRagEmptyResults:
    @pytest.mark.asyncio
    async def test_empty_search_returns_empty_snippets(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestRagErrorHandling:
    @pytest.mark.asyncio
    async def test_client_error_returns_empty_context_no_raise(self):
        mock = MagicMock()
        mock.search = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))

        with _patch_rag_client(mock):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestStoreGeneratedPlaybook:
    @pytest.mark.asyncio
    async def test_stores_composite_document(self):
        mock_rag = MagicMock()
        mock_rag.upload_file = AsyncMock()

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-oom",
                playbook_yaml="- hosts: all\n  tasks: []",
                failure_type="OOMKilled",
                summary="Container killed by OOM",
            )

        call = mock_rag.upload_file.call_args
        content = call[0][1]
        expected = (
            "Failure: OOMKilled\n"
            "Summary: Container killed by OOM\n"
            "Playbook (OOMKilled):\n"
            "- hosts: all\n  tasks: []"
        )
        assert content == expected.encode()

    @pytest.mark.asyncio
    async def test_uses_playbook_name_as_filename(self):
        mock_rag = MagicMock()
        mock_rag.upload_file = AsyncMock()

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="remediate-crash",
                playbook_yaml="yaml content",
                failure_type="CrashLoop",
                summary="Pod crashing",
            )

        call = mock_rag.upload_file.call_args
        assert call[0][0] == "remediate-crash.md"

    @pytest.mark.asyncio
    async def test_passes_chunking_params(self):
        mock_rag = MagicMock()
        mock_rag.upload_file = AsyncMock()

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-dns",
                playbook_yaml="tasks: []",
                failure_type="DNSFailure",
                summary="DNS resolution failed",
            )

        mock_rag.upload_file.assert_awaited_once()
        call = mock_rag.upload_file.call_args
        assert "chunk_size_tokens" in call.kwargs
        assert "chunk_overlap_tokens" in call.kwargs

    @pytest.mark.asyncio
    async def test_logs_error_on_client_failure(self):
        mock_rag = MagicMock()
        mock_rag.upload_file = AsyncMock(
            side_effect=ConnectionError("upload failed"),
        )

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-crash",
                playbook_yaml="tasks: []",
                failure_type="CrashLoop",
                summary="Pod crashing",
            )
