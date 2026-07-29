from dataclasses import dataclass

from telco_oran.domain.cell import Cell


@dataclass
class RanKpiRecord:
    """A single RAN KPI measurement snapshot for a cell on a specific frequency band."""

    cell: Cell
    datetime: datetime
    band: str
    frequency: str
    ues_usage: int
    rsrp: float
    rsrq: float
    sinr: float
    throughput_mbps: float
    latency_ms: float
