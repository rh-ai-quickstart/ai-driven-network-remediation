"""Compatibility checks for the RCA-to-remediation enriched-anomaly contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from ran_remediation_service.nodes.decide import _CATEGORY_TO_TEMPLATE


def test_every_contract_root_cause_category_has_a_playbook_template():
    contract_path = Path(__file__).parents[3] / "contracts" / "ran-anomaly-enriched.schema.json"
    contract = json.loads(contract_path.read_text())
    categories = contract["properties"]["root_cause_category"]["enum"]

    assert set(categories) == _CATEGORY_TO_TEMPLATE.keys()
    assert len(set(_CATEGORY_TO_TEMPLATE.values())) == len(categories)


def test_each_predicted_label_has_a_seeded_playbook_file():
    root = Path(__file__).parents[3]
    spec = importlib.util.spec_from_file_location("aap_mock_contract", root / "hub/infra/aap-mock/main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    templates = {item["name"]: item["playbook"] for item in module.job_templates_db.values()}

    for template in _CATEGORY_TO_TEMPLATE.values():
        assert template in templates
        assert (root / templates[template]).is_file()
