from abc import ABC, abstractmethod
from dataclasses import dataclass

from telco_oran.domain.ran_kpi_record import RanKpiRecord


@dataclass
class Anomaly(ABC):
    """Abstract base class for all RAN KPI anomalies."""

    record: RanKpiRecord

    @abstractmethod
    def __str__(self) -> str: ...


@dataclass
class HighPrbUtilization(Anomaly):
    """PRB utilization exceeds 95% of max capacity."""

    utilization_pct: float
    threshold: float = 95.0

    def __str__(self) -> str:
        return f"High PRB Utilization: {self.utilization_pct:.2f}% > {self.threshold}%"


@dataclass
class LowRsrp(Anomaly):
    """Reference Signal Received Power below acceptable level."""

    rsrp: float
    threshold: float = -110.0

    def __str__(self) -> str:
        return f"Low RSRP: {self.rsrp} dBm < {self.threshold} dBm"


@dataclass
class SinrDegradation(Anomaly):
    """Signal-to-Interference-plus-Noise Ratio below acceptable level."""

    sinr: float
    threshold: float = 0.0

    def __str__(self) -> str:
        return f"Low SINR: {self.sinr} dB < {self.threshold} dB"


@dataclass
class ThroughputDrop(Anomaly):
    """Current throughput dropped more than 50% compared to historical average."""

    throughput_mbps: float
    avg_prior_throughput_mbps: float
    threshold_pct: float = 50.0

    def __str__(self) -> str:
        return (
            f"Throughput Drop: {self.throughput_mbps:.2f} Mbps (Current) "
            f"vs. {self.avg_prior_throughput_mbps:.2f} Mbps (Avg Prior) "
            f"- drop > {self.threshold_pct:.0f}%"
        )


@dataclass
class UesSpikeOrDrop(Anomaly):
    """UEs usage changed more than 50% compared to historical average."""

    ues_usage: int
    avg_prior_ues: float
    threshold_pct: float = 50.0

    def __str__(self) -> str:
        return (
            f"UEs Spike/Drop: {self.ues_usage} UEs (Current) "
            f"vs. {self.avg_prior_ues:.2f} UEs (Avg Prior) "
            f"- change > {self.threshold_pct:.0f}%"
        )


@dataclass
class CellOutage(Anomaly):
    """All cell metrics indicate a complete outage."""

    sinr: float
    rsrp: float
    rsrq: float

    def __str__(self) -> str:
        return f"Cell Outage: UEs=0, Tput=0, " f"SINR={self.sinr}, RSRP={self.rsrp}, RSRQ={self.rsrq}"
