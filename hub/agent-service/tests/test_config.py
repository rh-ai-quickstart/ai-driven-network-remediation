import importlib

import pytest


def test_kafka_defaults_match_alert_topics():
    from agent_service.kafka.alerts import ALERT_TOPICS

    config = importlib.reload(importlib.import_module("agent_service.config"))

    assert config.KAFKA_BOOTSTRAP == "kafka:9092"
    assert set(config.KAFKA_CONSUME_TOPICS) == set(ALERT_TOPICS)
    assert config.KAFKA_GROUP_ID == "dark-noc-agent"
    assert config.KAFKA_AUDIT_TOPIC == "incident-audit"
    assert config.KAFKA_CONSUMER_ENABLED is True


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("GRAPH_INVOKE_TIMEOUT_SECONDS", "9000")
    monkeypatch.setenv("KAFKA_BOOTSTRAP", "kafka.example:9092")
    monkeypatch.setenv("KAFKA_CONSUME_TOPICS", "noc-alerts, system-alerts")
    monkeypatch.setenv("KAFKA_GROUP_ID", "test-group")
    monkeypatch.setenv("KAFKA_AUDIT_TOPIC", "custom-audit")
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "false")

    config = importlib.reload(importlib.import_module("agent_service.config"))

    assert config.KAFKA_BOOTSTRAP == "kafka.example:9092"
    assert config.KAFKA_CONSUME_TOPICS == ["noc-alerts", "system-alerts"]
    assert config.KAFKA_GROUP_ID == "test-group"
    assert config.KAFKA_AUDIT_TOPIC == "custom-audit"
    assert config.KAFKA_CONSUMER_ENABLED is False
    assert config.GRAPH_INVOKE_TIMEOUT_SECONDS == 9000


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_consumer_enabled_truthy_values(monkeypatch, value):
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", value)
    config = importlib.reload(importlib.import_module("agent_service.config"))
    assert config.KAFKA_CONSUMER_ENABLED is True


def test_workflow_timeout_tracks_lightspeed_budget(monkeypatch):
    config = importlib.import_module("agent_service.config")
    with monkeypatch.context() as environment:
        environment.delenv("GRAPH_INVOKE_TIMEOUT_SECONDS", raising=False)
        environment.setenv("LIGHTSPEED_TIMEOUT_SECONDS", "600")
        try:
            importlib.reload(config)
            assert config.GRAPH_INVOKE_TIMEOUT_SECONDS == 1800
        finally:
            environment.undo()
            importlib.reload(config)
