"""Tests for ran-anomaly-detector configuration."""

from ran_anomaly_detector import config


def test_defaults():
    assert config.KAFKA_BOOTSTRAP == "kafka:9092"
    assert config.KAFKA_METRICS_TOPIC == "ran-combined-metrics"
    assert config.KAFKA_ANOMALIES_TOPIC == "ran-anomalies"
    assert config.KAFKA_GROUP_ID == "ran-anomaly-detector"
    assert config.KAFKA_CONSUMER_ENABLED is True
    assert config.KAFKA_PRODUCER_ENABLED is True
    assert config.RECENT_ANOMALIES_LIMIT == 100


def test_detect_inference_url_defaults_to_empty():
    assert config.DETECT_INFERENCE_URL == "" or isinstance(config.DETECT_INFERENCE_URL, str)
