"""The mirrored RCA class order follows the notebook and independent predictor."""

import ast
import json
from pathlib import Path

from telco_oran.rca import RCA_CLASSES

ROOT = Path(__file__).resolve().parents[3]
REFERENCE_CLASSES = (
    "Antenna Failure", "Co-Channel Interference (Mild)",
    "Co-Channel Interference (Severe)", "Faulty RF Filters (Temporal)",
    "Doppler Shift (Severe)", "Faulty Handover Algorithm (Too Frequent)",
    "Buffer Overflow (Gradual Buildup)", "Resource Allocation Bugs",
    "High Network Congestion (Gradual Buildup)", "High Network Congestion (Sudden Spike)",
)


def _assignment_value(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "tuple"
                and len(node.value.args) == 1
                and not node.value.keywords
                and isinstance(node.value.args[0], ast.Name)
            ):
                return tuple(_assignment_value(tree, node.value.args[0].id))
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} missing")


def test_notebook_and_self_contained_predictor_match_reference_order():
    notebook = json.loads((ROOT / "model-serving/shared-ml-predictor/notebooks/telecomts_model_evaluation.ipynb").read_text())
    source = next("".join(cell["source"]) for cell in notebook["cells"] if "RCA_CLASSES = [" in "".join(cell["source"]))
    notebook_classes = _assignment_value(ast.parse(source), "RCA_CLASSES")
    predictor_source = (ROOT / "model-serving/shared-ml-predictor/src/ran_ml_service/model.py").read_text()
    predictor_classes = _assignment_value(ast.parse(predictor_source), "RCA_CLASSES")
    classifier_source = (ROOT / "hub/ran-rca-service/src/ran_rca_service/nodes/classify.py").read_text()
    classifier_classes = _assignment_value(ast.parse(classifier_source), "RCA_CLASSES")
    assert tuple(notebook_classes) == RCA_CLASSES == tuple(predictor_classes) == tuple(classifier_classes) == REFERENCE_CLASSES
