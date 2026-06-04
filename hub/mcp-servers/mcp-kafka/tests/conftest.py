import pytest


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    from mcp_kafka import config

    monkeypatch.setattr(config, "KAFKA_BOOTSTRAP", "localhost:9092")
    monkeypatch.setattr(
        config,
        "KAFKA_CONSUME_TOPICS",
        ["system-alerts", "noc-alerts", "remediation-jobs"],
    )
    monkeypatch.setattr(
        config,
        "KAFKA_PRODUCE_TOPICS",
        ["remediation-jobs", "agent-events", "incident-audit"],
    )
    monkeypatch.setattr(config, "MAX_MESSAGES_CAP", 100)
    monkeypatch.setattr(config, "MAX_TIMEOUT_MS_CAP", 15_000)
