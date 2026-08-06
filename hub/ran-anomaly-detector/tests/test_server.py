from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from ran_anomaly_detector.server import app


@pytest.fixture
def client():
    with patch("ran_anomaly_detector.server.MetricsConsumer"):
        with TestClient(app) as test_client:
            yield test_client


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestReadyEndpoint:
    @patch("ran_anomaly_detector.server.KAFKA_CONSUMER_ENABLED", False)
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

    def test_anomalies_returns_detected_items(self, client):
        service = client.app.state.detection_service
        csv_blob = (
            "cell_id,max_capacity,lat,lon,area_type,city,band,frequency,datetime,"
            "ues_usage,rsrp,rsrq,sinr,throughput_mbps,latency_ms\n"
            "42,100,33.05,-96.8,industrial,Plano,Band 29,700,2026-07-29T10:00:00Z,10,"
            "-125.0,-15.0,5.0,50.0,20.0\n"
        )
        outputs = service.process_csv(csv_blob)
        client.app.state.recent_anomalies.extend(outputs)

        response = client.get("/anomalies")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["anomalies"][0]["anomaly_type"] == "LowRsrp"

    def test_anomalies_respects_limit(self, client):
        for i in range(5):
            client.app.state.recent_anomalies.append(
                {"cell_id": i, "band": "Band 29", "anomaly_type": "X", "anomaly": "x"}
            )

        response = client.get("/anomalies", params={"limit": 2})

        body = response.json()
        assert body["count"] == 2
        assert [a["cell_id"] for a in body["anomalies"]] == [3, 4]


class TestKafkaLifespan:
    @patch("ran_anomaly_detector.server.MetricsConsumer")
    @patch.multiple(
        "ran_anomaly_detector.server",
        KAFKA_CONSUMER_ENABLED=True,
        KAFKA_BOOTSTRAP="kafka.test:9092",
        KAFKA_METRICS_TOPIC="ran-combined-metrics",
        KAFKA_GROUP_ID="test-group",
    )
    def test_lifespan_starts_consumer_when_enabled(self, MetricsConsumer):
        mock_consumer = MagicMock()
        MetricsConsumer.return_value = mock_consumer

        with TestClient(app):
            pass

        MetricsConsumer.assert_called_once()
        _, kwargs = MetricsConsumer.call_args
        assert kwargs["bootstrap_servers"] == "kafka.test:9092"
        assert kwargs["topic"] == "ran-combined-metrics"
        assert kwargs["group_id"] == "test-group"
        mock_consumer.start.assert_called_once()
        mock_consumer.stop.assert_called_once()

    @patch("ran_anomaly_detector.server.MetricsConsumer")
    @patch("ran_anomaly_detector.server.KAFKA_CONSUMER_ENABLED", False)
    def test_lifespan_skips_consumer_when_disabled(self, MetricsConsumer):
        with TestClient(app):
            pass

        MetricsConsumer.assert_not_called()

    @patch("ran_anomaly_detector.server.MetricsConsumer")
    @patch("ran_anomaly_detector.server.KAFKA_CONSUMER_ENABLED", True)
    def test_lifespan_wires_handler_into_recent_anomalies(self, MetricsConsumer):
        captured_handler = {}

        def _capture(handler, **kwargs):
            captured_handler["handler"] = handler
            return MagicMock()

        MetricsConsumer.side_effect = _capture

        with TestClient(app) as client:
            csv_blob = (
                "cell_id,max_capacity,lat,lon,area_type,city,band,frequency,datetime,"
                "ues_usage,rsrp,rsrq,sinr,throughput_mbps,latency_ms\n"
                "42,100,33.05,-96.8,industrial,Plano,Band 29,700,2026-07-29T10:00:00Z,10,"
                "-125.0,-15.0,5.0,50.0,20.0\n"
            ).encode("utf-8")

            captured_handler["handler"](csv_blob)

            response = client.get("/anomalies")
            assert response.json()["count"] == 1
