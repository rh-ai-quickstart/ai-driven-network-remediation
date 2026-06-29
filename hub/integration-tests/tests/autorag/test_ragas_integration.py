"""Integration tests for individual RAGAS eval API endpoints.

Tests dataset CRUD, benchmark CRUD, scoring-functions listing, and error
handling against a running AutoRAG service via the Llama Stack eval API.
"""

import uuid

import httpx
import pytest

_RAGAS_PROVIDER_ID = "trustyai_ragas_inline"
_RAGAS_METRICS = ["faithfulness", "answer_relevancy", "context_precision"]


def _unique_id(prefix: str = "ragas-integ-test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _safe_delete(client: httpx.Client, path: str) -> None:
    """DELETE ignoring server connection resets (Llama Stack server bug)."""
    try:
        client.delete(path)
    except (httpx.ReadError, httpx.ConnectError):
        pass


def _sample_eval_rows() -> list[dict]:
    return [
        {
            "user_input": "What is OSPF?",
            "retrieved_contexts": [
                "OSPF is a link-state routing protocol used within an AS."
            ],
            "response": "OSPF is a routing protocol for IP networks.",
            "reference": "OSPF is a link-state routing protocol used within an AS.",
        }
    ]


def _create_dataset(client: httpx.Client, dataset_id: str) -> httpx.Response:
    return client.post(
        "/v1beta/datasets",
        json={
            "dataset_id": dataset_id,
            "purpose": "eval/messages-answer",
            "source": {"type": "rows", "rows": _sample_eval_rows()},
        },
    )


def _create_benchmark(
    client: httpx.Client,
    benchmark_id: str,
    dataset_id: str,
    *,
    provider_id: str = _RAGAS_PROVIDER_ID,
    scoring_functions: list[str] | None = None,
) -> httpx.Response:
    body: dict = {
        "benchmark_id": benchmark_id,
        "dataset_id": dataset_id,
        "scoring_functions": scoring_functions or _RAGAS_METRICS,
    }
    if provider_id is not None:
        body["provider_id"] = provider_id
    return client.post("/v1alpha/eval/benchmarks", json=body)


def _eval_providers(client: httpx.Client) -> list[dict]:
    resp = client.get("/v1/providers")
    if resp.status_code != 200:
        return []
    data = resp.json()
    providers = data if isinstance(data, list) else data.get("data", [])
    return [p for p in providers if p.get("api") == "eval"]


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDatasetCRUD:
    """Create, list, get, and delete datasets via /v1beta/datasets."""

    @pytest.fixture(autouse=True)
    def _setup_ids(self):
        self.dataset_id = _unique_id("ragas-integ-test-ds")

    @pytest.fixture()
    def created_dataset(self, autorag_client):
        resp = _create_dataset(autorag_client, self.dataset_id)
        assert resp.status_code == 200, f"Dataset setup failed: {resp.text}"
        yield resp.json()
        autorag_client.delete(f"/v1beta/datasets/{self.dataset_id}")

    def test_create_dataset(self, autorag_client):
        resp = _create_dataset(autorag_client, self.dataset_id)
        assert resp.status_code == 200, f"Dataset creation failed: {resp.text}"
        autorag_client.delete(f"/v1beta/datasets/{self.dataset_id}")

    def test_list_datasets(self, autorag_client, created_dataset):
        resp = autorag_client.get("/v1beta/datasets")
        assert resp.status_code == 200, f"List datasets failed: {resp.text}"
        data = resp.json()
        datasets = data if isinstance(data, list) else data.get("data", [])
        ids = [
            d.get("identifier") or d.get("dataset_id") or d.get("id")
            for d in datasets
        ]
        assert self.dataset_id in ids, (
            f"Dataset '{self.dataset_id}' not found in list: {ids}"
        )

    def test_get_dataset_by_id(self, autorag_client, created_dataset):
        resp = autorag_client.get(f"/v1beta/datasets/{self.dataset_id}")
        assert resp.status_code == 200, f"Get dataset failed: {resp.text}"
        body = resp.json()
        ds_id = body.get("identifier") or body.get("dataset_id") or body.get("id")
        assert ds_id == self.dataset_id

    def test_delete_dataset(self, autorag_client):
        create_resp = _create_dataset(autorag_client, self.dataset_id)
        assert create_resp.status_code == 200, f"Dataset setup failed: {create_resp.text}"

        del_resp = autorag_client.delete(f"/v1beta/datasets/{self.dataset_id}")
        assert del_resp.status_code in (200, 204), f"Delete failed: {del_resp.text}"

        get_resp = autorag_client.get(f"/v1beta/datasets/{self.dataset_id}")
        assert get_resp.status_code in (404, 400), (
            f"Dataset still exists after delete (status={get_resp.status_code})"
        )

    def test_create_duplicate_dataset_is_idempotent(self, autorag_client, created_dataset):
        dup_resp = _create_dataset(autorag_client, self.dataset_id)
        assert dup_resp.status_code == 200, (
            f"Duplicate dataset creation should be idempotent, got {dup_resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Benchmark CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBenchmarkCRUD:
    """Create, list, get, and delete benchmarks via /v1alpha/eval/benchmarks."""

    @pytest.fixture(autouse=True)
    def _setup_ids(self):
        suffix = uuid.uuid4().hex[:8]
        self.dataset_id = f"ragas-integ-test-bm-ds-{suffix}"
        self.benchmark_id = f"ragas-integ-test-bm-{suffix}"

    @pytest.fixture()
    def created_benchmark(self, autorag_client):
        ds_resp = _create_dataset(autorag_client, self.dataset_id)
        assert ds_resp.status_code == 200, f"Dataset setup failed: {ds_resp.text}"

        bm_resp = _create_benchmark(
            autorag_client, self.benchmark_id, self.dataset_id
        )
        assert bm_resp.status_code == 200, f"Benchmark setup failed: {bm_resp.text}"

        yield bm_resp.json()

        autorag_client.delete(
            f"/v1alpha/eval/benchmarks/{self.benchmark_id}"
        )
        autorag_client.delete(f"/v1beta/datasets/{self.dataset_id}")

    def test_create_benchmark(self, autorag_client):
        ds_resp = _create_dataset(autorag_client, self.dataset_id)
        assert ds_resp.status_code == 200, f"Dataset setup failed: {ds_resp.text}"

        bm_resp = _create_benchmark(
            autorag_client, self.benchmark_id, self.dataset_id
        )
        assert bm_resp.status_code == 200, f"Benchmark creation failed: {bm_resp.text}"

        autorag_client.delete(f"/v1alpha/eval/benchmarks/{self.benchmark_id}")
        autorag_client.delete(f"/v1beta/datasets/{self.dataset_id}")

    def test_list_benchmarks(self, autorag_client, created_benchmark):
        resp = autorag_client.get("/v1alpha/eval/benchmarks")
        assert resp.status_code == 200, f"List benchmarks failed: {resp.text}"
        data = resp.json()
        benchmarks = data if isinstance(data, list) else data.get("data", [])
        ids = [
            b.get("identifier") or b.get("benchmark_id") or b.get("id")
            for b in benchmarks
        ]
        assert self.benchmark_id in ids, (
            f"Benchmark '{self.benchmark_id}' not found in list: {ids}"
        )

    def test_get_benchmark_by_id(self, autorag_client, created_benchmark):
        resp = autorag_client.get(
            f"/v1alpha/eval/benchmarks/{self.benchmark_id}"
        )
        assert resp.status_code == 200, f"Get benchmark failed: {resp.text}"
        body = resp.json()
        bm_id = body.get("identifier") or body.get("benchmark_id") or body.get("id")
        assert bm_id == self.benchmark_id

    def test_delete_benchmark(self, autorag_client):
        ds_resp = _create_dataset(autorag_client, self.dataset_id)
        assert ds_resp.status_code == 200, f"Dataset setup failed: {ds_resp.text}"

        bm_resp = _create_benchmark(
            autorag_client, self.benchmark_id, self.dataset_id
        )
        assert bm_resp.status_code == 200, f"Benchmark setup failed: {bm_resp.text}"

        try:
            del_resp = autorag_client.delete(
                f"/v1alpha/eval/benchmarks/{self.benchmark_id}"
            )
            assert del_resp.status_code in (200, 204), f"Delete failed: {del_resp.text}"
        except httpx.ReadError:
            pytest.xfail("Llama Stack resets connection on benchmark DELETE")

        try:
            get_resp = autorag_client.get(
                f"/v1alpha/eval/benchmarks/{self.benchmark_id}"
            )
            assert get_resp.status_code in (404, 400), (
                f"Benchmark still exists after delete (status={get_resp.status_code})"
            )
        except httpx.ReadError:
            pass

        _safe_delete(autorag_client, f"/v1beta/datasets/{self.dataset_id}")

    def test_create_benchmark_requires_provider_id(self, autorag_client):
        eval_providers = _eval_providers(autorag_client)
        if len(eval_providers) < 2:
            pytest.skip("Only one eval provider registered; provider_id ambiguity cannot be tested")

        ds_resp = _create_dataset(autorag_client, self.dataset_id)
        assert ds_resp.status_code == 200, f"Dataset setup failed: {ds_resp.text}"

        bm_resp = _create_benchmark(
            autorag_client,
            self.benchmark_id,
            self.dataset_id,
            provider_id=None,
        )
        assert bm_resp.status_code >= 400, (
            f"Expected error when omitting provider_id with multiple eval providers, "
            f"got {bm_resp.status_code}"
        )

        _safe_delete(autorag_client, f"/v1alpha/eval/benchmarks/{self.benchmark_id}")
        _safe_delete(autorag_client, f"/v1beta/datasets/{self.dataset_id}")


# ---------------------------------------------------------------------------
# Scoring Functions API
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestScoringFunctionsAPI:
    """Verify the /v1/scoring-functions endpoint."""

    def test_scoring_functions_endpoint_exists(self, autorag_client):
        resp = autorag_client.get("/v1/scoring-functions")
        assert resp.status_code == 200, (
            f"scoring-functions endpoint returned {resp.status_code}: {resp.text}"
        )

    def test_scoring_functions_returns_list(self, autorag_client):
        resp = autorag_client.get("/v1/scoring-functions")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data, f"Response missing 'data' key: {data.keys()}"
        assert isinstance(data["data"], list)

    def test_scoring_functions_have_required_fields(self, autorag_client):
        resp = autorag_client.get("/v1/scoring-functions")
        assert resp.status_code == 200
        functions = resp.json().get("data", [])
        assert functions, "No scoring functions returned"
        for fn in functions:
            assert "identifier" in fn, f"Missing 'identifier' in: {fn}"
            assert "provider_id" in fn, f"Missing 'provider_id' in: {fn}"
            assert "type" in fn, f"Missing 'type' in: {fn}"


# ---------------------------------------------------------------------------
# Benchmark Error Handling
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBenchmarkErrorHandling:
    """Verify error responses for invalid benchmark operations."""

    def test_create_benchmark_with_nonexistent_dataset_is_permissive(self, autorag_client):
        bm_id = _unique_id("ragas-integ-test-bad-bm")
        resp = _create_benchmark(
            autorag_client, bm_id, "nonexistent-dataset-id-12345"
        )
        assert resp.status_code == 200, (
            f"Benchmark creation with nonexistent dataset failed: {resp.status_code}"
        )
        _safe_delete(autorag_client, f"/v1alpha/eval/benchmarks/{bm_id}")

    def test_submit_job_to_nonexistent_benchmark(self, autorag_client):
        try:
            resp = autorag_client.post(
                "/v1alpha/eval/benchmarks/nonexistent-benchmark-12345/jobs",
                json={
                    "benchmark_config": {
                        "eval_candidate": {
                            "type": "model",
                            "model": "dummy-model",
                            "sampling_params": {"max_tokens": 64},
                        }
                    }
                },
            )
        except httpx.ReadError:
            pytest.xfail("Llama Stack resets connection on nonexistent benchmark job submit")
            return
        assert resp.status_code in (404, 400, 500), (
            f"Expected error for nonexistent benchmark, got {resp.status_code}"
        )

    def test_get_nonexistent_job(self, autorag_client):
        try:
            resp = autorag_client.get(
                "/v1alpha/eval/benchmarks/nonexistent-benchmark-12345/jobs/nonexistent-job-12345"
            )
        except httpx.ReadError:
            pytest.xfail("Llama Stack resets connection on nonexistent job GET")
            return
        assert resp.status_code in (404, 400, 500), (
            f"Expected error for nonexistent job, got {resp.status_code}"
        )
