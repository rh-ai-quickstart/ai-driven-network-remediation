from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ingestion_pipeline.clients.llamastack import DuplicateVectorStoreNameError, LlamaStackVectorStoreClient
from ingestion_pipeline.service import EmptyRunbookCorpusError, IngestionPipelineService


class Page:
    def __init__(self, data, next_page=None):
        self.data = data
        self._next_page = next_page

    def has_next_page(self):
        return self._next_page is not None

    def get_next_page(self):
        return self._next_page


def _client(stores_page, files_page=None):
    client = object.__new__(LlamaStackVectorStoreClient)
    client._vector_store_name = "noc_runbooks"
    client._client = SimpleNamespace(
        vector_stores=SimpleNamespace(
            list=Mock(return_value=stores_page),
            create=Mock(),
            files=SimpleNamespace(list=Mock(return_value=files_page), delete=Mock()),
        )
    )
    return client


def _store(id, name="noc_runbooks"):
    return SimpleNamespace(id=id, name=name, status="completed", file_counts=SimpleNamespace(model_dump=dict))


def test_store_lookup_scans_all_pages_for_exact_name():
    client = _client(Page([_store("wrong", "noc_runbooks_backup")], Page([_store("target")])))

    assert client.ensure_vector_store().id == "target"
    client._client.vector_stores.create.assert_not_called()


def test_duplicate_store_names_fail_before_any_attachment_is_removed():
    client = _client(Page([_store("first")], Page([_store("second")])))

    with pytest.raises(DuplicateVectorStoreNameError, match="Multiple vector stores named 'noc_runbooks'"):
        client.ensure_vector_store()
    client._client.vector_stores.files.delete.assert_not_called()


def test_clear_removes_every_attachment_from_all_pages():
    files = Page([SimpleNamespace(id="generated")], Page([SimpleNamespace(id="runbook")]))
    client = _client(Page([_store("target")]), files)

    client.clear_vector_store_files("target")

    assert [call.args[0] for call in client._client.vector_stores.files.delete.call_args_list] == [
        "generated", "runbook"
    ]


def test_failed_detach_stops_before_replacement():
    client = _client(Page([_store("target")]), Page([SimpleNamespace(id="generated")]))
    client._client.vector_stores.files.delete.return_value = SimpleNamespace(deleted=False)

    with pytest.raises(RuntimeError, match="Failed to detach file 'generated'"):
        client.clear_vector_store_files("target")


def test_empty_source_preserves_existing_attachments():
    service = object.__new__(IngestionPipelineService)
    service._ingesting = False
    service._minio_client = SimpleNamespace(load_prefix_text_objects=Mock(return_value=[]))
    service._vector_client = SimpleNamespace(ensure_vector_store=Mock(), clear_vector_store_files=Mock())

    with pytest.raises(EmptyRunbookCorpusError, match="No runbooks found"):
        service.ingest()

    service._vector_client.ensure_vector_store.assert_not_called()
    service._vector_client.clear_vector_store_files.assert_not_called()
    assert service._ingesting is False
