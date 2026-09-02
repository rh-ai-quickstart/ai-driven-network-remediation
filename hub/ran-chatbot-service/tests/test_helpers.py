"""Unit tests for helper functions: chat formatting.

build_deps() and normalize_session_id() are tested in hub/shared/tests
instead, since that's where they now live. Kafka consumption itself
(AnomaliesConsumer) is tested separately in test_kafka_consumer.py.
"""

import httpx
import pytest
import respx
from ran_chatbot_service.chat import build_chat_context, call_model, format_chat_reply
from ran_chatbot_service.config import MODEL_API_URL
from ran_chatbot_service.models import ModelSource


class TestBuildChatContext:
    def test_includes_anomaly_details(self, sample_anomalies):
        prompt = build_chat_context("What's happening?", sample_anomalies, [])
        assert "test-001" in prompt
        assert "zone=A" in prompt
        assert "Twitch" in prompt
        assert "0.94" in prompt
        assert "Signal degradation" in prompt
        assert "Section 4.2" in prompt
        assert "What's happening?" in prompt

    def test_handles_no_anomalies(self):
        prompt = build_chat_context("Any issues?", [], [])
        assert "No recent RAN anomalies detected." in prompt

    def test_includes_recent_conversation_history(self):
        history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        prompt = build_chat_context("next question", [], history)
        assert "user: hello" in prompt
        assert "assistant: hi" in prompt

    def test_blank_root_cause_and_fix_render_as_na(self, sample_anomaly):
        anomaly = sample_anomaly.model_copy(update={"root_cause": "", "recommended_fix": ""})
        prompt = build_chat_context("What's wrong?", [anomaly], [])
        assert "Root cause: n/a" in prompt
        assert "Recommended fix: n/a" in prompt

    def test_uses_the_five_most_recent_anomalies(self, sample_anomaly):
        anomalies = [sample_anomaly.model_copy(update={"incident_id": f"inc-{i}"}) for i in range(7)]
        prompt = build_chat_context("Status?", anomalies, [])
        for i in range(2, 7):
            assert f"inc-{i}" in prompt
        for i in range(0, 2):
            assert f"Incident inc-{i} " not in prompt


class TestCallModel:
    @pytest.mark.asyncio
    @respx.mock
    async def test_live_reply(self):
        respx.post(MODEL_API_URL).mock(
            return_value=httpx.Response(200, json={"choices": [{"text": "Incident shows signal degradation."}]})
        )
        async with httpx.AsyncClient() as client:
            reply, source = await call_model("prompt", client)

        assert reply == "Incident shows signal degradation."
        assert source == ModelSource.LIVE

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_reported_with_status_code(self):
        respx.post(MODEL_API_URL).mock(return_value=httpx.Response(404))
        async with httpx.AsyncClient() as client:
            reply, source = await call_model("prompt", client)

        assert reply == ""
        assert source == "http-404"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_choices_reported_as_empty(self):
        respx.post(MODEL_API_URL).mock(return_value=httpx.Response(200, json={"choices": []}))
        async with httpx.AsyncClient() as client:
            reply, source = await call_model("prompt", client)

        assert reply == ""
        assert source == ModelSource.EMPTY

    @pytest.mark.asyncio
    @respx.mock
    async def test_connection_error_reported_as_unreachable(self):
        respx.post(MODEL_API_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        async with httpx.AsyncClient() as client:
            reply, source = await call_model("prompt", client)

        assert reply == ""
        assert source == ModelSource.UNREACHABLE

    @pytest.mark.asyncio
    @respx.mock
    async def test_reuses_the_same_client_instance_across_multiple_calls(self):
        route = respx.post(MODEL_API_URL).mock(return_value=httpx.Response(200, json={"choices": [{"text": "ok"}]}))
        async with httpx.AsyncClient() as client:
            first = await call_model("prompt one", client)
            second = await call_model("prompt two", client)

        assert first == ("ok", ModelSource.LIVE)
        assert second == ("ok", ModelSource.LIVE)
        assert route.call_count == 2


class TestFormatChatReply:
    def test_with_anomalies_and_live_reply(self, sample_anomalies):
        reply = format_chat_reply("What's wrong?", "Signal degradation in zone A.", sample_anomalies)
        assert "Anomalies detected: 1" in reply
        assert "Incident test-001" in reply
        assert "zone=A" in reply
        assert "AD confidence: 0.94" in reply
        assert "Signal degradation" in reply

    def test_with_no_anomalies(self):
        reply = format_chat_reply("Any issues?", "All clear.", [])
        assert "Anomalies detected: 0" in reply
        assert "No RAN anomalies currently detected." in reply

    def test_falls_back_when_model_reply_is_empty(self):
        reply = format_chat_reply("Status?", "", [])
        assert "fallback" in reply.lower()

    def test_blank_root_cause_and_fix_render_as_na(self, sample_anomaly):
        anomaly = sample_anomaly.model_copy(update={"root_cause": "", "recommended_fix": ""})
        reply = format_chat_reply("What's wrong?", "insight", [anomaly])
        assert "Root Cause:\n- n/a" in reply
        assert "Recommended Fix:\n- n/a" in reply

    def test_latest_anomaly_is_the_last_element_not_the_first(self, sample_anomaly):
        oldest = sample_anomaly.model_copy(update={"incident_id": "inc-old"})
        newest = sample_anomaly.model_copy(update={"incident_id": "inc-new"})
        reply = format_chat_reply("What's wrong?", "insight", [oldest, newest])
        assert "Incident inc-new" in reply
