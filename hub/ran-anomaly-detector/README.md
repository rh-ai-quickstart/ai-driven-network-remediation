# ran-anomaly-detector

ML-based RAN/O-RAN anomaly detection orchestrator. Consumes TelecomTS JSON samples (18 KPI channels
× 128 timesteps) from the `ran-combined-metrics` Kafka topic, calls the `ran-ml-service` predictor
via HTTP (`POST /v1/detect`), and publishes typeless anomalies to `ran-anomalies` only when the
Mantis AD model says anomalous.

This is a stream client — it does not run the model itself. The model runs in `ran-ml-service`
(a separate FastAPI service backed by Mantis-8M on OpenShift AI or local `uv run`).

## Output

For each detected anomaly, one JSON record is published to `ran-anomalies`:

```json
{
  "incident_id": "a3f7c2d1",
  "zone": "A",
  "application": "Twitch",
  "kpi_window": [ /* 128 timesteps × 18 channels */ ],
  "ad_label": "anomalous",
  "ad_confidence": 0.9995
}
```

Normal samples (ad_label="normal") are **never** published — only anomalous windows reach
downstream (ran-rca-service, chatbot).

See `contracts/ran-anomalies.schema.json` for the full JSON Schema.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `DETECT_INFERENCE_URL` | *(required)* | Full URL to the detect predictor (e.g., `http://hub-ran-ml-service:8080/v1/detect`) |
| `KAFKA_BOOTSTRAP` | `kafka:9092` | Kafka bootstrap servers |
| `KAFKA_METRICS_TOPIC` | `ran-combined-metrics` | Input topic (JSON TelecomTS samples) |
| `KAFKA_ANOMALIES_TOPIC` | `ran-anomalies` | Output topic (anomalies only) |
| `KAFKA_CONSUMER_ENABLED` | `true` | Enable Kafka consumption |
| `KAFKA_PRODUCER_ENABLED` | `true` | Enable Kafka publishing |
| `RECENT_ANOMALIES_LIMIT` | `100` | In-memory buffer size for `/anomalies` endpoint |

## Readiness

`/ready` returns 503 unless **both**:
1. Kafka consumer is connected
2. The detect predictor (`DETECT_INFERENCE_URL`) is reachable and reports ready

This means Kubernetes will not send traffic to this detector until the ML model is loaded and
serving. If the predictor pod restarts, this detector will go not-ready until it's back.

## Usage

```bash
cd hub/ran-anomaly-detector
uv sync --group dev
uv run pytest
```

## Architecture

```
Kafka (ran-combined-metrics)
    │ JSON TelecomTS sample
    ▼
ran-anomaly-detector
    │ POST /v1/detect { kpi_window }
    ▼
ran-ml-service (Mantis AD)
    │ { label: "anomalous", confidence: 0.99 }
    ▼
ran-anomaly-detector
    │ publish to ran-anomalies (only if anomalous)
    ▼
Kafka (ran-anomalies)
```
