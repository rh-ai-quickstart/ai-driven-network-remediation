"""Orchestrates turning a raw RAN metrics Kafka message into anomaly output records."""

from __future__ import annotations

from typing import Any

from loguru import logger
from ran_anomaly_detector.csv_mapper import parse_csv_records
from ran_anomaly_detector.metrics_store import DEFAULT_HISTORY_SIZE, MetricsStore
from telco_oran.domain.anomaly import Anomaly
from telco_oran.domain.anomaly_detector import AnomalyDetector

AnomalyOutput = dict[str, Any]


def anomaly_to_output(anomaly: Anomaly) -> AnomalyOutput:
    """Map a detected Anomaly to the agreed-upon output JSON shape.

    {"cell_id": 42, "band": "Band 29", "anomaly_type": "LowRsrp", "anomaly": "Low RSRP: ..."}
    """
    record = anomaly.record
    return {
        "cell_id": record.cell.cell_id,
        "band": record.band,
        "anomaly_type": type(anomaly).__name__,
        "anomaly": str(anomaly),
    }


class AnomalyDetectionService:
    """Stateful orchestration: raw CSV metrics -> domain objects -> detected anomalies.

    Holds the rolling per-(cell_id, band) history across calls, since a single Kafka
    message only ever carries a slice of the ongoing stream of readings. This is
    entirely rule-based (telco_oran.AnomalyDetector) — no LLM involved.
    """

    def __init__(self, history_size: int = DEFAULT_HISTORY_SIZE) -> None:
        self._store = MetricsStore(history_size=history_size)
        self._detector = AnomalyDetector()

    def process_csv(self, raw_csv: str) -> list[AnomalyOutput]:
        records = parse_csv_records(raw_csv)
        outputs: list[AnomalyOutput] = []

        for kpi_record in records:
            metrics = self._store.update(kpi_record)
            anomalies = self._detector.detect(metrics)
            outputs.extend(anomaly_to_output(anomaly) for anomaly in anomalies)

        if outputs:
            logger.info(
                "Detected {} RAN anomalies from batch of {} record(s)",
                len(outputs),
                len(records),
            )

        return outputs

    def process_message(self, raw_value: bytes) -> list[AnomalyOutput]:
        """Decode a raw Kafka message value and run it through detection."""
        if not raw_value or not raw_value.strip():
            return []
        try:
            raw_csv = raw_value.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping non-UTF-8 RAN metrics Kafka message")
            return []
        return self.process_csv(raw_csv)
