"""Classifier boundary, guidance threshold, and category independence."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from helpers import make_state
from ran_rca_service.nodes.classify import RCA_CLASSES, classify_node


@pytest.mark.asyncio
async def test_confident_prediction_uses_only_window_and_optional_token():
    response = MagicMock()
    response.json.return_value = {"class": RCA_CLASSES[0], "class_index": 0, "confidence": 0.6}
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__.return_value = client
    with (
        patch("ran_rca_service.config.CLASSIFY_INFERENCE_URL", "https://example/v1/classify"),
        patch("ran_rca_service.config.CLASSIFY_TOKEN", "secret"),
        patch("ran_rca_service.nodes.classify.httpx.AsyncClient", return_value=client),
    ):
        state = make_state()
        result = await classify_node(state)
    assert result["ml_status"] == "used"
    assert result["ml_steer_used"] is True
    assert client.post.call_args.kwargs["json"] == {"kpi_window": state.kpi_window}
    assert client.post.call_args.kwargs["headers"] == {"Authorization": "Bearer secret"}


@pytest.mark.asyncio
@pytest.mark.parametrize("prediction", [
    [],
    {"class": RCA_CLASSES[0], "class_index": 0, "confidence": 0.59},
    {"class": RCA_CLASSES[1], "class_index": 0, "confidence": 0.99},
    {"class": RCA_CLASSES[0], "class_index": True, "confidence": 0.99},
    {"class": RCA_CLASSES[0], "class_index": 0, "confidence": float("nan")},
])
async def test_untrusted_or_low_confidence_prediction_does_not_steer(prediction):
    response = MagicMock()
    response.json.return_value = prediction
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__.return_value = client
    with (
        patch("ran_rca_service.config.CLASSIFY_INFERENCE_URL", "https://example/v1/classify"),
        patch("ran_rca_service.config.CLASSIFY_TOKEN", ""),
        patch("ran_rca_service.nodes.classify.httpx.AsyncClient", return_value=client),
    ):
        result = await classify_node(make_state())
    assert result["ml_steer_used"] is False
    assert result["ml_status"] in {"low_confidence", "unavailable"}
    assert client.post.call_args.kwargs["headers"] == {}


@pytest.mark.asyncio
async def test_disabled_classifier_preserves_llm_only_mode():
    with patch("ran_rca_service.config.CLASSIFY_INFERENCE_URL", ""):
        result = await classify_node(make_state(ml_class="stale", ml_steer_used=True))
    assert result == {"ml_class": "", "ml_class_index": None, "ml_confidence": None, "ml_status": "disabled", "ml_steer_used": False}
