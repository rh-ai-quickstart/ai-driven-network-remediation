import asyncio
from unittest.mock import DEFAULT, AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_service.models import IncidentState
from agent_service.server import _invoke_graph_for_alert, app

INCIDENT_STATE_FIELDS = set(IncidentState.model_fields.keys())


@pytest.fixture
def client():
    with (
        patch("agent_service.server.AlertConsumer"),
        patch("agent_service.nodes.audit.KafkaProducer"),
        patch("agent_service.server.warm_tool_cache", return_value=True),
    ):
        with TestClient(app) as test_client:
            yield test_client


async def _mock_escalate_invoke(tool_name, kwargs):
    if tool_name == "create_incident":
        return {"success": True, "number": "INC0000001"}
    return {}


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestReadyEndpoint:
    @patch("agent_service.server.KAFKA_CONSUMER_ENABLED", False)
    def test_ready_skips_kafka_when_consumer_disabled(self, client):
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"ready": True}

    def test_ready_returns_true_when_consumer_connected(self, client):
        mock_consumer = MagicMock()
        mock_consumer.is_connected = True
        client.app.state.kafka_consumer = mock_consumer
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"ready": True}

    def test_ready_returns_503_when_consumer_not_connected(self, client):
        mock_consumer = MagicMock()
        mock_consumer.is_connected = False
        client.app.state.kafka_consumer = mock_consumer
        response = client.get("/ready")
        assert response.status_code == 503
        assert "kafka" in response.json()["reason"]

    def test_ready_returns_503_when_llamastack_not_ready(self, client):
        client.app.state.llamastack_ready = False
        with patch(
            "agent_service.server.warm_tool_cache",
            return_value=False,
        ):
            response = client.get("/ready")
        assert response.status_code == 503
        assert "llamastack" in response.json()["reason"]

    def test_ready_retries_llamastack_on_probe(self, client):
        client.app.state.llamastack_ready = False
        with patch(
            "agent_service.server.warm_tool_cache",
            return_value=True,
        ):
            response = client.get("/ready")
        assert response.status_code == 200
        assert client.app.state.llamastack_ready is True


class TestRemediateEndpoint:
    def test_post_remediate_returns_full_state(self, client):
        with patch("agent_service.nodes.escalate._invoke_tool", _mock_escalate_invoke):
            response = client.post("/remediate", json={"raw_event": "test event"})
        assert response.status_code == 200
        body = response.json()
        assert body["raw_event"] == "test event"
        assert set(body.keys()) == INCIDENT_STATE_FIELDS
        assert body["decision"] != ""

    def test_post_remediate_with_failure_type_override(self, client):
        response = client.post(
            "/remediate",
            json={"raw_event": "test event", "confidence_override": 0.9, "failure_type_override": "KafkaLag"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "lightspeed"

    def test_post_remediate_rejects_invalid_failure_type_override(self, client):
        response = client.post(
            "/remediate",
            json={"raw_event": "test event", "failure_type_override": "FooBar"},
        )
        assert response.status_code == 422

    def test_post_remediate_rejects_missing_raw_event(self, client):
        response = client.post("/remediate", json={})
        assert response.status_code == 422


class TestKafkaLifespan:
    @patch("agent_service.server.warm_tool_cache", return_value=True)
    @patch("agent_service.server.AlertConsumer")
    @patch.multiple(
        "agent_service.server",
        KAFKA_CONSUMER_ENABLED=True,
        KAFKA_BOOTSTRAP="kafka.test:9092",
        KAFKA_CONSUME_TOPICS=["noc-alerts", "system-alerts"],
        KAFKA_GROUP_ID="test-group",
        build_graph=DEFAULT,
    )
    def test_lifespan_starts_consumer_when_enabled(self, AlertConsumer, _warm, **_mocks):
        mock_consumer = MagicMock()
        AlertConsumer.return_value = mock_consumer

        with TestClient(app):
            pass

        AlertConsumer.assert_called_once()
        _, kwargs = AlertConsumer.call_args
        assert kwargs["bootstrap_servers"] == "kafka.test:9092"
        assert kwargs["topics"] == ["noc-alerts", "system-alerts"]
        assert kwargs["group_id"] == "test-group"
        mock_consumer.start.assert_called_once()
        mock_consumer.stop.assert_called_once()

    @patch("agent_service.server.warm_tool_cache", return_value=True)
    @patch("agent_service.server.AlertConsumer")
    @patch("agent_service.server.KAFKA_CONSUMER_ENABLED", False)
    @patch("agent_service.server.build_graph", return_value=MagicMock())
    def test_lifespan_skips_consumer_when_disabled(self, _build_graph, AlertConsumer, _warm):
        with TestClient(app):
            pass

        AlertConsumer.assert_not_called()

    def test_invoke_graph_for_alert_passes_kafka_offset(self):
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={"incident_id": "inc-123"})

        alert = MagicMock(topic="system-alerts", offset=42, raw_event='{"message":"oom"}')

        async def run_from_consumer_thread() -> None:
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(_invoke_graph_for_alert, alert, graph, loop)

        asyncio.run(run_from_consumer_thread())

        graph.ainvoke.assert_called_once_with(
            {
                "raw_event": '{"message":"oom"}',
                "kafka_offset": 42,
            }
        )

    def test_invoke_graph_for_alert_uses_main_event_loop(self):
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={"incident_id": "inc-456"})

        alert = MagicMock(topic="system-alerts", offset=7, raw_event='{"message":"oom"}')
        captured_loops: list[asyncio.AbstractEventLoop] = []

        async def run_from_consumer_thread(main_loop: asyncio.AbstractEventLoop) -> None:
            await asyncio.to_thread(_invoke_graph_for_alert, alert, graph, main_loop)
            captured_loops.append(asyncio.get_running_loop())

        async def main() -> None:
            main_loop = asyncio.get_running_loop()
            await run_from_consumer_thread(main_loop)
            assert captured_loops[0] is main_loop

        asyncio.run(main())
        graph.ainvoke.assert_called_once()

    @patch("agent_service.server.GRAPH_INVOKE_TIMEOUT_SECONDS", 0.01)
    def test_invoke_graph_for_alert_times_out(self):
        invoke_count = 0

        async def slow_invoke(_payload):
            nonlocal invoke_count
            invoke_count += 1
            await asyncio.sleep(1)
            return {"incident_id": "never"}

        graph = MagicMock()
        graph.ainvoke = slow_invoke

        alert = MagicMock(topic="system-alerts", offset=99, raw_event='{"message":"oom"}')

        async def run_from_consumer_thread() -> None:
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(_invoke_graph_for_alert, alert, graph, loop)

        asyncio.run(run_from_consumer_thread())
        assert invoke_count == 1
