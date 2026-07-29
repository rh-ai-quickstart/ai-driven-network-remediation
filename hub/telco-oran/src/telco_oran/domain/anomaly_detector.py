from telco_oran.domain.anomaly import (
    Anomaly,
    CellOutage,
    HighPrbUtilization,
    LowRsrp,
    SinrDegradation,
    ThroughputDrop,
    UesSpikeOrDrop,
)
from telco_oran.domain.cell_band_metrics import CellBandMetrics


class AnomalyDetector:
    """Detects RAN KPI anomalies from a CellBandMetrics aggregate."""

    def detect(self, metrics: CellBandMetrics) -> list[Anomaly]:
        record = metrics.latest
        if record is None:
            return []

        anomalies: list[Anomaly] = []

        self._check_high_prb_utilization(record, anomalies)
        self._check_low_rsrp(record, anomalies)
        self._check_sinr_degradation(record, anomalies)
        self._check_cell_outage(record, anomalies)
        self._check_throughput_drop(record, metrics, anomalies)
        self._check_ues_spike_or_drop(record, metrics, anomalies)

        return anomalies

    def _check_high_prb_utilization(self, record, anomalies):
        max_capacity = record.cell.max_capacity
        if max_capacity <= 0:
            return

        utilization_pct = (record.ues_usage / max_capacity) * 100
        if utilization_pct > HighPrbUtilization.threshold:
            anomalies.append(HighPrbUtilization(record=record, utilization_pct=utilization_pct))

    def _check_low_rsrp(self, record, anomalies):
        if record.rsrp < LowRsrp.threshold:
            anomalies.append(LowRsrp(record=record, rsrp=record.rsrp))

    def _check_sinr_degradation(self, record, anomalies):
        if record.sinr < SinrDegradation.threshold:
            anomalies.append(SinrDegradation(record=record, sinr=record.sinr))

    def _check_cell_outage(self, record, anomalies):
        if (
            record.ues_usage == 0
            and record.throughput_mbps == 0
            and record.sinr <= -10
            and record.rsrp <= -120
            and record.rsrq <= -20
        ):
            anomalies.append(
                CellOutage(
                    record=record,
                    sinr=record.sinr,
                    rsrp=record.rsrp,
                    rsrq=record.rsrq,
                )
            )

    def _check_throughput_drop(self, record, metrics, anomalies):
        history = metrics.history
        if len(history) < 3:
            return

        avg_prior = sum(r.throughput_mbps for r in history[-3:]) / 3
        threshold_fraction = ThroughputDrop.threshold_pct / 100
        if avg_prior > 0 and record.throughput_mbps < threshold_fraction * avg_prior:
            anomalies.append(
                ThroughputDrop(
                    record=record,
                    throughput_mbps=record.throughput_mbps,
                    avg_prior_throughput_mbps=avg_prior,
                )
            )

    def _check_ues_spike_or_drop(self, record, metrics, anomalies):
        history = metrics.history
        if len(history) < 3:
            return

        avg_prior = sum(r.ues_usage for r in history[-3:]) / 3
        threshold_fraction = UesSpikeOrDrop.threshold_pct / 100
        if avg_prior > 0 and abs(record.ues_usage - avg_prior) / avg_prior > threshold_fraction:
            anomalies.append(
                UesSpikeOrDrop(
                    record=record,
                    ues_usage=record.ues_usage,
                    avg_prior_ues=avg_prior,
                )
            )
