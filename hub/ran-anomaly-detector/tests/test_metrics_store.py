from datetime import datetime, timezone

import pytest
from ran_anomaly_detector.metrics_store import MetricsStore
from telco_oran.domain.cell import Cell
from telco_oran.domain.ran_kpi_record import RanKpiRecord


def _cell(cell_id=1, max_capacity=100) -> Cell:
    return Cell(
        cell_id=cell_id,
        max_capacity=max_capacity,
        lat=33.0,
        lon=-97.0,
        bands=["Band 29"],
        area_type="industrial",
        city="Plano",
        adjacent_cells=[],
    )


def _record(cell: Cell, band="Band 29", throughput_mbps=100.0) -> RanKpiRecord:
    return RanKpiRecord(
        cell=cell,
        datetime=datetime.now(timezone.utc),
        band=band,
        frequency="700",
        ues_usage=50,
        rsrp=-95.0,
        rsrq=-10.0,
        sinr=15.0,
        throughput_mbps=throughput_mbps,
        latency_ms=20.0,
    )


def test_update_returns_metrics_containing_the_new_record():
    store = MetricsStore(history_size=10)
    cell = _cell()
    record = _record(cell)

    metrics = store.update(record)

    assert metrics.cell is cell
    assert metrics.band == "Band 29"
    assert metrics.records == [record]
    assert metrics.latest is record


def test_history_accumulates_across_updates_for_same_key():
    store = MetricsStore(history_size=10)
    cell = _cell()
    first = _record(cell, throughput_mbps=100.0)
    second = _record(cell, throughput_mbps=90.0)

    store.update(first)
    metrics = store.update(second)

    assert metrics.records == [first, second]
    assert metrics.latest is second
    assert metrics.history == [first]


def test_history_window_is_bounded():
    store = MetricsStore(history_size=3)
    cell = _cell()

    metrics = None
    for i in range(5):
        metrics = store.update(_record(cell, throughput_mbps=float(i)))

    assert len(metrics.records) == 3
    # Only the last 3 readings (throughput 2.0, 3.0, 4.0) should remain.
    assert [r.throughput_mbps for r in metrics.records] == [2.0, 3.0, 4.0]


def test_different_cell_band_keys_have_independent_windows():
    store = MetricsStore(history_size=10)
    cell_1 = _cell(cell_id=1)
    cell_2 = _cell(cell_id=2)

    store.update(_record(cell_1, band="Band 29"))
    store.update(_record(cell_1, band="Band 66"))
    metrics_cell2 = store.update(_record(cell_2, band="Band 29"))

    assert len(metrics_cell2.records) == 1

    metrics_cell1_band29 = store.update(_record(cell_1, band="Band 29"))
    assert len(metrics_cell1_band29.records) == 2


def test_invalid_history_size_raises():
    with pytest.raises(ValueError):
        MetricsStore(history_size=0)
