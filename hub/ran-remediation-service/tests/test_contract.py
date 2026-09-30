"""Compatibility checks for the RCA-to-remediation enriched-anomaly contract."""

from __future__ import annotations

import json
from pathlib import Path

from ran_remediation_service.nodes.decide import _CATEGORY_TO_TEMPLATE


def test_every_contract_root_cause_category_has_a_playbook_template():
    contract_path = Path(__file__).parents[3] / "contracts" / "ran-anomaly-enriched.schema.json"
    contract = json.loads(contract_path.read_text())
    categories = contract["properties"]["root_cause_category"]["enum"]

    assert set(categories) <= _CATEGORY_TO_TEMPLATE.keys()
