"""Tests for the RAN RCA service FastAPI server."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from helpers import SAMPLE_ANOMALY
from ran_rca_service.server import _handle_anomaly_message, app


@pytest.fixture
def client():
    with patch("ran_rca_service.server.TopicConsumer"), patch("ran_rca_service.server.KafkaProducer"):
        with TestClient(app) as test_client:
            yield test_client


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestReadyEndpoint:
    @patch("ran_rca_service.server.KAFKA_CONSUMER_ENABLED", False)
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


class TestAnomaliesEndpoint:
    def test_anomalies_empty_by_default(self, client):
        response = client.get("/anomalies")
        assert response.status_code == 200
        assert response.json() == {"count": 0, "anomalies": []}

    def test_anomalies_returns_enriched_items(self, client):
        client.app.state.recent_enriched.append(
            {**SAMPLE_ANOMALY, "root_cause": "stub", "recommended_fix": "stub"}
        )

        response = client.get("/anomalies")
        body = response.json()
        assert body["count"] == 1
        assert body["anomalies"][0]["root_cause"] == "stub"

    def test_anomalies_respects_limit(self, client):
        for i in range(5):
            client.app.state.recent_enriched.append(
                {"cell_id": i, "band": "Band 29", "anomaly_type": "X", "anomaly": "x", "root_cause": "r", "recommended_fix": "f"}
            )

        response = client.get("/anomalies", params={"limit": 2})
        body = response.json()
        assert body["count"] == 2
        assert [a["cell_id"] for a in body["anomalies"]] == [3, 4]

    def test_anomalies_limit_zero_returns_empty(self, client):
        client.app.state.recent_enriched.append(
            {**SAMPLE_ANOMALY, "root_cause": "r", "recommended_fix": "f"}
        )

        response = client.get("/anomalies", params={"limit": 0})
        body = response.json()
        assert body["count"] == 0
        assert body["anomalies"] == []


class TestHandleAnomalyMessage:
    def test_invokes_graph_and_appends_to_buffer(self):
        from collections import deque

        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={
            **SAMPLE_ANOMALY,
            "context_snippets": [],
            "rag_query_used": SAMPLE_ANOMALY["anomaly"],
            "root_cause": "stub root cause",
            "recommended_fix": "stub fix",
        })
        producer = MagicMock()
        buffer: deque = deque(maxlen=100)

        raw = json.dumps(SAMPLE_ANOMALY).encode("utf-8")
        _handle_anomaly_message(raw, graph, producer, "ran-anomalies-enriched", buffer)

        graph.ainvoke.assert_awaited_once_with(SAMPLE_ANOMALY)
        assert len(buffer) == 1
        assert buffer[0]["root_cause"] == "stub root cause"

    def test_publishes_enriched_to_kafka(self):
        from collections import deque

        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={
            **SAMPLE_ANOMALY,
            "context_snippets": [],
            "rag_query_used": "",
            "root_cause": "cause",
            "recommended_fix": "fix",
        })
        producer = MagicMock()
        buffer: deque = deque(maxlen=100)

        raw = json.dumps(SAMPLE_ANOMALY).encode("utf-8")
        _handle_anomaly_message(raw, graph, producer, "ran-anomalies-enriched", buffer)

        producer.send.assert_called_once()
        topic, payload = producer.send.call_args[0]
        assert topic == "ran-anomalies-enriched"
        enriched = json.loads(payload)
        assert enriched["root_cause"] == "cause"
        assert enriched["recommended_fix"] == "fix"

    def test_skips_malformed_json(self):
        from collections import deque

        graph = MagicMock()
        producer = MagicMock()
        buffer: deque = deque(maxlen=100)

        _handle_anomaly_message(b"not json", graph, producer, "topic", buffer)

        graph.ainvoke.assert_not_called()
        producer.send.assert_not_called()
        assert len(buffer) == 0

    def test_works_without_producer(self):
        from collections import deque

        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={
            **SAMPLE_ANOMALY,
            "context_snippets": [],
            "rag_query_used": "",
            "root_cause": "cause",
            "recommended_fix": "fix",
        })
        buffer: deque = deque(maxlen=100)

        raw = json.dumps(SAMPLE_ANOMALY).encode("utf-8")
        _handle_anomaly_message(raw, graph, None, "topic", buffer)

        assert len(buffer) == 1

    def test_graph_error_forwards_anomaly_unenriched(self):
        from collections import deque

        graph = MagicMock()
        graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph failure"))
        buffer: deque = deque(maxlen=100)

        raw = json.dumps(SAMPLE_ANOMALY).encode("utf-8")
        _handle_anomaly_message(raw, graph, None, "topic", buffer)

        assert len(buffer) == 1
        assert buffer[0]["cell_id"] == 42
        assert buffer[0]["root_cause"] == ""
        assert buffer[0]["recommended_fix"] == ""


class TestKafkaLifespan:
    @patch("ran_rca_service.server.KafkaProducer")
    @patch("ran_rca_service.server.TopicConsumer")
    @patch.multiple(
        "ran_rca_service.server",
        KAFKA_CONSUMER_ENABLED=True,
        KAFKA_BOOTSTRAP="kafka.test:9092",
        KAFKA_ANOMALIES_TOPIC="ran-anomalies",
        KAFKA_GROUP_ID="test-group",
    )
    def test_lifespan_starts_consumer_when_enabled(self, TopicConsumer, KafkaProducer):
        mock_consumer = MagicMock()
        TopicConsumer.return_value = mock_consumer

        with TestClient(app):
            pass

        TopicConsumer.assert_called_once()
        _, kwargs = TopicConsumer.call_args
        assert kwargs["bootstrap_servers"] == "kafka.test:9092"
        assert kwargs["topic"] == "ran-anomalies"
        assert kwargs["group_id"] == "test-group"
        mock_consumer.start.assert_called_once()
        mock_consumer.stop.assert_called_once()

    @patch("ran_rca_service.server.KafkaProducer")
    @patch("ran_rca_service.server.TopicConsumer")
    @patch("ran_rca_service.server.KAFKA_CONSUMER_ENABLED", False)
    def test_lifespan_skips_consumer_when_disabled(self, TopicConsumer, KafkaProducer):
        with TestClient(app):
            pass

        TopicConsumer.assert_not_called()

    @patch("ran_rca_service.server.KafkaProducer")
    @patch("ran_rca_service.server.TopicConsumer")
    @patch("ran_rca_service.server.KAFKA_CONSUMER_ENABLED", True)
    def test_lifespan_wires_handler_into_enriched_buffer(self, TopicConsumer, KafkaProducer):
        captured_handler = {}

        def _capture(handler, **kwargs):
            captured_handler["handler"] = handler
            return MagicMock()

        TopicConsumer.side_effect = _capture

        mock_rag = MagicMock()
        mock_rag.search = AsyncMock(return_value=[])

        with (
            patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock_rag),
            TestClient(app) as client,
        ):
            raw = json.dumps(SAMPLE_ANOMALY).encode("utf-8")
            captured_handler["handler"](raw)

            response = client.get("/anomalies")
            assert response.json()["count"] == 1
