import random as random_module

from telco_oran.domain.cell import Cell
from telco_oran.domain.cell_band_metrics import CellBandMetrics
from telco_oran.domain.ran_kpi_record import RanKpiRecord
from telco_oran.domain.ran_kpi_record_factory import RanKpiRecordFactory

AVAILABLE_BANDS = ["Band 29", "Band 26", "Band 71", "Band 66"]
AREA_TYPES = ["industrial", "commercial", "rural", "residential"]
CITIES = [
    "Frisco",
    "Plano",
    "McKinney",
    "Denton",
    "Allen",
    "Carrollton",
    "Irving",
    "Coppell",
    "Celina",
    "Prosper",
]


class SimulationFactory:
    """Generates a full simulation dataset of CellBandMetrics."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random_module.Random(seed)
        self._kpi_factory = RanKpiRecordFactory(seed)

    def create(
        self,
        num_cells: int,
        bands_per_cell: int,
        records_per_band: int,
    ) -> list[CellBandMetrics]:
        cells = self._generate_cells(num_cells, bands_per_cell)

        all_metrics: list[CellBandMetrics] = []
        for cell in cells:
            for band in cell.bands:
                records = self._generate_band_records(cell, band, records_per_band)
                all_metrics.append(CellBandMetrics(cell=cell, band=band, records=records))

        return all_metrics

    def _generate_band_records(self, cell: Cell, band: str, count: int) -> list[RanKpiRecord]:
        records = []
        for _ in range(count):
            kpi_records = self._kpi_factory.create_for_cell(cell)
            matching = [r for r in kpi_records if r.band == band]
            records.extend(matching)
        return records

    def _generate_cells(self, num_cells: int, bands_per_cell: int) -> list[Cell]:
        cells: list[Cell] = []
        for cell_id in range(num_cells):
            num_bands = min(bands_per_cell, len(AVAILABLE_BANDS))
            bands = self._rng.sample(AVAILABLE_BANDS, num_bands)

            cell = Cell(
                cell_id=cell_id,
                max_capacity=self._rng.randint(50, 100),
                lat=round(self._rng.uniform(33.0, 33.3), 6),
                lon=round(self._rng.uniform(-97.2, -96.5), 6),
                bands=bands,
                area_type=self._rng.choice(AREA_TYPES),
                city=self._rng.choice(CITIES),
                adjacent_cells=self._rng.sample(
                    [i for i in range(num_cells) if i != cell_id],
                    k=min(self._rng.randint(0, 3), max(num_cells - 1, 0)),
                ),
            )
            cells.append(cell)

        return cells
