from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from llama_stack_client import LlamaStackClient

logger = logging.getLogger(__name__)

RAGAS_PROVIDER_ID = "trustyai_ragas_inline"


@dataclass(frozen=True)
class RagasScores:
    aggregated: dict[str, float]
    per_row: list[dict[str, float]]


@dataclass(frozen=True)
class RagasResult:
    scores: dict[str, RagasScores]
    generations: list[dict[str, Any]]


class RagasEvaluationClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 120) -> None:
        self._client = LlamaStackClient(base_url=base_url, timeout=timeout_seconds)

    def evaluate(
        self,
        *,
        evaluation_data: list[dict[str, Any]],
        scoring_functions: list[str],
        model_id: str,
        dataset_id: str | None = None,
        benchmark_id: str | None = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
    ) -> RagasResult:
        if dataset_id is None:
            dataset_id = f"adnr_ragas_eval_{uuid.uuid4().hex[:8]}"
        if benchmark_id is None:
            benchmark_id = f"adnr_ragas_benchmark_{uuid.uuid4().hex[:8]}"

        self._register_dataset(dataset_id, evaluation_data)
        self._register_benchmark(benchmark_id, dataset_id, scoring_functions)
        job = self._run_eval(benchmark_id, model_id)
        self._wait_for_job(benchmark_id, job.job_id, poll_interval, poll_timeout)
        return self._get_results(benchmark_id, job.job_id)

    def _register_dataset(
        self, dataset_id: str, rows: list[dict[str, Any]]
    ) -> None:
        try:
            self._client.beta.datasets.unregister(dataset_id)
        except Exception:
            pass

        self._client.beta.datasets.register(
            dataset_id=dataset_id,
            purpose="eval/question-answer",
            source={"type": "rows", "rows": rows},
            metadata={
                "provider_id": "localfs",
                "description": "ADNR RAG evaluation dataset",
                "size": len(rows),
                "format": "ragas",
                "created_at": datetime.now().isoformat(),
            },
        )
        logger.info(f"Registered dataset '{dataset_id}' with {len(rows)} rows")

    def _register_benchmark(
        self,
        benchmark_id: str,
        dataset_id: str,
        scoring_functions: list[str],
    ) -> None:
        try:
            self._client.alpha.benchmarks.unregister(benchmark_id)
        except Exception:
            pass

        self._client.alpha.benchmarks.register(
            benchmark_id=benchmark_id,
            dataset_id=dataset_id,
            scoring_functions=scoring_functions,
            provider_id=RAGAS_PROVIDER_ID,
        )
        logger.info(f"Registered benchmark '{benchmark_id}' with metrics {scoring_functions}")

    def _run_eval(self, benchmark_id: str, model_id: str) -> Any:
        job = self._client.alpha.eval.run_eval(
            benchmark_id=benchmark_id,
            benchmark_config={
                "eval_candidate": {
                    "type": "model",
                    "model": model_id,
                    "sampling_params": {"temperature": 0.1, "max_tokens": 512},
                },
                "scoring_params": {},
            },
        )
        logger.info(f"Started RAGAS evaluation job '{job.job_id}'")
        return job

    def _wait_for_job(
        self,
        benchmark_id: str,
        job_id: str,
        poll_interval: float,
        poll_timeout: float,
    ) -> None:
        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            status = self._client.alpha.eval.jobs.status(
                benchmark_id=benchmark_id, job_id=job_id
            )
            if status.status != "in_progress":
                if status.status != "completed":
                    raise RuntimeError(
                        f"RAGAS job '{job_id}' failed with status '{status.status}'"
                    )
                logger.info(f"RAGAS job '{job_id}' completed with status '{status.status}'")
                return
            time.sleep(poll_interval)
        raise TimeoutError(f"RAGAS job '{job_id}' did not complete within {poll_timeout}s")

    def _get_results(self, benchmark_id: str, job_id: str) -> RagasResult:
        result = self._client.alpha.eval.jobs.retrieve(
            benchmark_id=benchmark_id, job_id=job_id
        )
        scores: dict[str, RagasScores] = {}
        for metric_name, scoring_result in (result.scores or {}).items():
            aggregated = {}
            if isinstance(scoring_result.aggregated_results, dict):
                aggregated = scoring_result.aggregated_results
            per_row = scoring_result.score_rows or []
            scores[metric_name] = RagasScores(
                aggregated=aggregated,
                per_row=per_row,
            )

        generations = []
        for gen in result.generations or []:
            if isinstance(gen, dict):
                generations.append(gen)
            else:
                generations.append(gen.model_dump() if hasattr(gen, "model_dump") else dict(gen))

        return RagasResult(scores=scores, generations=generations)
