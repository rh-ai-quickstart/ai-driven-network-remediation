# ran-ml-service

Mantis time-series ML predictor for TelecomTS anomaly detection (TASK=detect) and root cause analysis (TASK=classify).

## Endpoints

- `POST /v1/detect` — Binary anomaly detection on a 128x18 KPI window
- `GET /health` — Liveness probe
- `GET /ready` — Readiness probe (model loaded)

## Configuration

| Env Var | Description |
|---------|-------------|
| `TASK` | `detect` or `classify` |
| `MANTIS_MODEL_PATH` | Local path to `.pt` weights file |
| `MANTIS_CHECKPOINT` | HuggingFace backbone ID (default: `paris-noah/Mantis-8M`, baked into image) |
| `PORT` | HTTP port (default: 8080) |
