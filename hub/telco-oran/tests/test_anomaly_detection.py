from datetime import datetime

from telco_oran.domain.anomaly import (
    Anomaly,
    CellOutage,
    HighPrbUtilization,
    LowRsrp,
    SinrDegradation,
    ThroughputDrop,
    UesSpikeOrDrop,
)
from telco_oran.domain.anomaly_detector import AnomalyDetector
from telco_oran.domain.cell import Cell
from telco_oran.domain.cell_band_metrics import CellBandMetrics
from telco_oran.domain.ran_kpi_record import RanKpiRecord
from telco_oran.generation.simulation_factory import SimulationFactory

SEED = 42


def test_detect_anomalies_on_generated_data():
    factory = SimulationFactory(seed=SEED)
    metrics_list = factory.create(num_cells=5, bands_per_cell=2, records_per_band=4)

    detector = AnomalyDetector()
    all_anomalies: list[Anomaly] = []

    for metrics in metrics_list:
        anomalies = detector.detect(metrics)

        for anomaly in anomalies:
            assert isinstance(anomaly, Anomaly)
            assert anomaly.record is metrics.latest
            assert anomaly.record.cell is metrics.cell
            assert anomaly.record.band == metrics.band
            assert str(anomaly)

        all_anomalies.extend(anomalies)

    assert len(all_anomalies) == 2

    throughput_drop = all_anomalies[0]
    assert isinstance(throughput_drop, ThroughputDrop)
    assert throughput_drop.record.cell.cell_id == 0
    assert throughput_drop.record.band == "Band 66"
    assert throughput_drop.throughput_mbps == 18.89
    assert round(throughput_drop.avg_prior_throughput_mbps, 2) == 54.75

    low_rsrp = all_anomalies[1]
    assert isinstance(low_rsrp, LowRsrp)
    assert low_rsrp.record.cell.cell_id == 1
    assert low_rsrp.record.band == "Band 29"
    assert low_rsrp.rsrp == -110.05


def test_no_history_based_anomalies_when_history_too_short():
    """Verify that throughput-drop and UEs-spike checks are skipped
    when fewer than 3 history records are available."""
    cell = Cell(
        cell_id=99,
        max_capacity=100,
        lat=33.0,
        lon=-97.0,
        bands=["Band 29"],
        area_type="industrial",
        city="Plano",
        adjacent_cells=[],
    )

    high_throughput = RanKpiRecord(
        cell=cell,
        datetime=datetime.now(),
        band="Band 29",
        frequency="700",
        ues_usage=50,
        rsrp=-90.0,
        rsrq=-10.0,
        sinr=15.0,
        throughput_mbps=100.0,
        latency_ms=20.0,
    )
    low_throughput = RanKpiRecord(
        cell=cell,
        datetime=datetime.now(),
        band="Band 29",
        frequency="700",
        ues_usage=50,
        rsrp=-90.0,
        rsrq=-10.0,
        sinr=15.0,
        throughput_mbps=5.0,
        latency_ms=20.0,
    )

    metrics = CellBandMetrics(
        cell=cell,
        band="Band 29",
        records=[high_throughput, low_throughput],
    )

    anomalies = AnomalyDetector().detect(metrics)
    assert len(anomalies) == 0


def _make_cell(**overrides) -> Cell:
    defaults = dict(
        cell_id=1,
        max_capacity=100,
        lat=33.0,
        lon=-97.0,
        bands=["Band 29"],
        area_type="industrial",
        city="Plano",
        adjacent_cells=[],
    )
    defaults.update(overrides)
    return Cell(**defaults)


def _make_record(cell: Cell, **overrides) -> RanKpiRecord:
    defaults = dict(
        cell=cell,
        datetime=datetime.now(),
        band="Band 29",
        frequency="700",
        ues_usage=50,
        rsrp=-90.0,
        rsrq=-10.0,
        sinr=15.0,
        throughput_mbps=100.0,
        latency_ms=20.0,
    )
    defaults.update(overrides)
    return RanKpiRecord(**defaults)


def test_detect_high_prb_utilization():
    cell = _make_cell(max_capacity=100)
    record = _make_record(cell, ues_usage=96)
    metrics = CellBandMetrics(cell=cell, band="Band 29", records=[record])

    anomalies = AnomalyDetector().detect(metrics)

    assert len(anomalies) == 1
    assert isinstance(anomalies[0], HighPrbUtilization)
    assert anomalies[0].utilization_pct == 96.0


def test_detect_sinr_degradation():
    cell = _make_cell()
    record = _make_record(cell, sinr=-2.5)
    metrics = CellBandMetrics(cell=cell, band="Band 29", records=[record])

    anomalies = AnomalyDetector().detect(metrics)

    assert len(anomalies) == 1
    assert isinstance(anomalies[0], SinrDegradation)
    assert anomalies[0].sinr == -2.5


def test_detect_cell_outage():
    """A full outage also triggers LowRsrp and SinrDegradation."""
    cell = _make_cell()
    record = _make_record(
        cell,
        ues_usage=0,
        throughput_mbps=0.0,
        sinr=-15.0,
        rsrp=-125.0,
        rsrq=-22.0,
    )
    metrics = CellBandMetrics(cell=cell, band="Band 29", records=[record])

    anomalies = AnomalyDetector().detect(metrics)

    assert any(isinstance(a, CellOutage) for a in anomalies)
    assert any(isinstance(a, LowRsrp) for a in anomalies)
    assert any(isinstance(a, SinrDegradation) for a in anomalies)
    outage = next(a for a in anomalies if isinstance(a, CellOutage))
    assert outage.sinr == -15.0
    assert outage.rsrp == -125.0
    assert outage.rsrq == -22.0


def test_detect_ues_spike_or_drop():
    cell = _make_cell()
    history = [_make_record(cell, ues_usage=50) for _ in range(3)]
    latest = _make_record(cell, ues_usage=10)
    metrics = CellBandMetrics(
        cell=cell,
        band="Band 29",
        records=history + [latest],
    )

    anomalies = AnomalyDetector().detect(metrics)

    assert any(isinstance(a, UesSpikeOrDrop) for a in anomalies)
    spike = next(a for a in anomalies if isinstance(a, UesSpikeOrDrop))
    assert spike.ues_usage == 10
    assert spike.avg_prior_ues == 50.0
