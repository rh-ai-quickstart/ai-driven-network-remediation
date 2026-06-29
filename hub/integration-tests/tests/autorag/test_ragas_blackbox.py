"""Black-box tests for the RAGAS evaluation provider via the Llama Stack eval API.

The RAGAS inline provider registers under the eval API (not the scoring API).
Metrics are evaluated via eval benchmarks + jobs, not via /v1/scoring/score.
"""

import httpx
import pytest

_RAGAS_PROVIDER_ID = "trustyai_ragas_inline"


def _eval_providers(client: httpx.Client) -> list[dict]:
    resp = client.get("/v1/providers")
    if resp.status_code != 200:
        return []
    data = resp.json()
    providers = data if isinstance(data, list) else data.get("data", [])
    return [p for p in providers if p.get("api") == "eval"]


@pytest.mark.e2e
class TestRagasProviderDiscovery:
    """The RAGAS inline provider is loaded and accessible."""

    def test_ragas_eval_provider_registered(self, autorag_client):
        providers = _eval_providers(autorag_client)
        ragas = [
            p for p in providers if p.get("provider_id") == _RAGAS_PROVIDER_ID
        ]
        assert ragas, (
            f"Provider '{_RAGAS_PROVIDER_ID}' not found in eval providers. "
            f"Available: {[p.get('provider_id') for p in providers]}"
        )

    def test_ragas_provider_has_embedding_config(self, autorag_client):
        providers = _eval_providers(autorag_client)
        ragas = next(
            (p for p in providers if p.get("provider_id") == _RAGAS_PROVIDER_ID),
            None,
        )
        assert ragas is not None
        config = ragas.get("config", {})
        assert config.get("embedding_model"), (
            f"RAGAS provider missing embedding_model config: {config}"
        )
