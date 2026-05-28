import pytest


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    from mcp_lokistack import client, config

    monkeypatch.setattr(config, "LOKI_URL", "http://localhost:3100")
    monkeypatch.setattr(config, "LOKI_TOKEN", "test-token")
    monkeypatch.setattr(config, "LOKI_TOKEN_PATH", "")
    monkeypatch.setattr(config, "LOKI_TLS_VERIFY", False)
    monkeypatch.setattr(config, "LOKI_CA_CERT_PATH", "")
    monkeypatch.setattr(config, "LOKI_DEFAULT_TENANT", "application")
    monkeypatch.setattr(config, "LOKI_MAX_LINES", 100)
    monkeypatch.setattr(config, "LOKI_MAX_LINES_CEILING", 500)
    monkeypatch.setattr(config, "LOKI_MAX_DURATION", "24h")
    monkeypatch.setattr(config, "LOKI_QUERY_TIMEOUT", 30)
    monkeypatch.setattr(config, "LOKI_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(config, "DEFAULT_SEVERITY_REGEX", r"(?i)(error|fatal|critical|panic|exception)")

    client._close_all()
    yield
    client._close_all()


SAMPLE_STREAMS_RESPONSE = {
    "status": "success",
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {
                    "namespace": "dark-noc-edge",
                    "app": "nginx",
                },
                "values": [
                    ["1716710400000000000", "error: connection refused"],
                    ["1716710300000000000", "warn: high latency detected"],
                    ["1716710200000000000", "error: timeout after 30s"],
                ],
            },
        ],
    },
}

SAMPLE_MATRIX_RESPONSE = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"kubernetes_namespace_name": "dark-noc-edge"},
                "values": [
                    [1716710400, "5"],
                    [1716710700, "12"],
                    [1716711000, "3"],
                ],
            },
        ],
    },
}

SAMPLE_LABELS_RESPONSE = {
    "status": "success",
    "data": ["dark-noc-edge", "monitoring", "default"],
}
