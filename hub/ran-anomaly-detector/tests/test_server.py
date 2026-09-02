"""Tests for the ran-anomaly-detector FastAPI server."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from ran_anomaly_detector.server import app


@pytest.fixture
def client():
    with patch("ran_anomaly_detector.server.TopicConsumer"), patch("ran_anomaly_detector.server.KafkaProducer"):
        with TestClient(app) as test_client:
            yield test_client


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestReadyEndpoint:
    @patch("ran_anomaly_detector.server.KAFKA_CONSUMER_ENABLED", False)
    @patch("ran_anomaly_detector.server.DETECT_INFERENCE_URL", "")
    def test_ready_when_kafka_disabled_and_no_predictor_url(self, client):
        """With no predictor URL configured, readiness skips the predictor check."""
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"ready": True}

    @patch("ran_anomaly_detector.server._check_predictor_ready", return_value=True)
    def test_ready_returns_true_when_all_deps_up(self, mock_pred, client):
        mock_consumer = MagicMock()
        mock_consumer.is_connected = True
        client.app.state.kafka_consumer = mock_consumer
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"ready": True}

    @patch("ran_anomaly_detector.server._check_predictor_ready", return_value=True)
    def test_ready_returns_503_when_kafka_not_connected(self, mock_pred, client):
        mock_consumer = MagicMock()
        mock_consumer.is_connected = False
        client.app.state.kafka_consumer = mock_consumer
        response = client.get("/ready")
        assert response.status_code == 503
        assert "kafka" in response.json()["reason"]

    @patch("ran_anomaly_detector.server._check_predictor_ready", return_value=False)
    def test_ready_returns_503_when_predictor_not_ready(self, mock_pred, client):
        mock_consumer = MagicMock()
        mock_consumer.is_connected = True
        client.app.state.kafka_consumer = mock_consumer
        response = client.get("/ready")
        assert response.status_code == 503
        assert "predictor" in response.json()["reason"]

    @patch("ran_anomaly_detector.server._check_predictor_ready", return_value=False)
    def test_ready_returns_503_with_both_deps_down(self, mock_pred, client):
        mock_consumer = MagicMock()
        mock_consumer.is_connected = False
        client.app.state.kafka_consumer = mock_consumer
        response = client.get("/ready")
        assert response.status_code == 503
        reason = response.json()["reason"]
        assert "kafka" in reason
        assert "predictor" in reason


class TestAnomaliesEndpoint:
    def test_anomalies_empty_by_default(self, client):
        response = client.get("/anomalies")
        assert response.status_code == 200
        assert response.json() == {"count": 0, "anomalies": []}

    def test_anomalies_returns_detected_items(self, client):
        client.app.state.recent_anomalies.append({
            "incident_id": "test-001",
            "zone": "A",
            "application": "Twitch",
            "kpi_window": [],
            "ad_label": "anomalous",
            "ad_confidence": 0.94,
        })
        response = client.get("/anomalies")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["anomalies"][0]["incident_id"] == "test-001"

    def test_anomalies_respects_limit(self, client):
        for i in range(5):
            client.app.state.recent_anomalies.append({
                "incident_id": f"inc-{i}",
                "zone": "A",
                "application": "X",
                "kpi_window": [],
                "ad_label": "anomalous",
                "ad_confidence": 0.9,
            })
        response = client.get("/anomalies", params={"limit": 2})
        body = response.json()
        assert body["count"] == 2
        assert [a["incident_id"] for a in body["anomalies"]] == ["inc-3", "inc-4"]


class TestKafkaLifespan:
    @patch("ran_anomaly_detector.server.KafkaProducer")
    @patch("ran_anomaly_detector.server.TopicConsumer")
    @patch.multiple(
        "ran_anomaly_detector.server",
        KAFKA_CONSUMER_ENABLED=True,
        KAFKA_BOOTSTRAP="kafka.test:9092",
        KAFKA_METRICS_TOPIC="ran-combined-metrics",
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
        assert kwargs["topic"] == "ran-combined-metrics"
        assert kwargs["group_id"] == "test-group"
        mock_consumer.start.assert_called_once()
        mock_consumer.stop.assert_called_once()

    @patch("ran_anomaly_detector.server.KafkaProducer")
    @patch("ran_anomaly_detector.server.TopicConsumer")
    @patch("ran_anomaly_detector.server.KAFKA_CONSUMER_ENABLED", False)
    def test_lifespan_skips_consumer_when_disabled(self, TopicConsumer, KafkaProducer):
        with TestClient(app):
            pass
        TopicConsumer.assert_not_called()

    @patch("ran_anomaly_detector.server.KafkaProducer")
    @patch("ran_anomaly_detector.server.TopicConsumer")
    @patch("ran_anomaly_detector.server.KAFKA_CONSUMER_ENABLED", True)
    @patch("ran_anomaly_detector.detection.DETECT_INFERENCE_URL", "http://predictor:8080/v1/detect")
    def test_handler_publishes_anomaly_to_kafka(self, TopicConsumer, KafkaProducer):
        import httpx

        mock_producer = MagicMock()
        KafkaProducer.return_value = mock_producer
        captured_handler = {}

        def _capture(handler, **kwargs):
            captured_handler["handler"] = handler
            return MagicMock()

        TopicConsumer.side_effect = _capture

        with patch("ran_anomaly_detector.detection._get_http_client") as mock_http:
            mock_http.return_value.post.return_value = httpx.Response(
                200, json={"label": "anomalous", "confidence": 0.92, "class_index": 1}
            )

            with TestClient(app):
                msg = json.dumps({
                    "incident_id": "pub-test",
                    "zone": "B",
                    "application": "YouTube",
                    "kpi_window": [{"RSRP": 0}] * 128,
                }).encode()
                captured_handler["handler"](msg)

                mock_producer.send.assert_called_once()
                topic, payload = mock_producer.send.call_args[0]
                assert topic == "ran-anomalies"
                published = json.loads(payload)
                assert published["incident_id"] == "pub-test"
                assert published["ad_label"] == "anomalous"
