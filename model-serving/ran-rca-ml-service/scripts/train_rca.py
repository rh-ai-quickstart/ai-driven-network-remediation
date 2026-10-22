"""Train and validate the pinned TelecomTS RCA task without running AD baselines.

Run from ``model-serving/shared-ml-predictor`` with
``uv run --group train python ../ran-rca-ml-service/scripts/train_rca.py --output-dir PATH``.
PATH must be a private, durable directory. The script never publishes a model.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import random
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from datasets import Dataset, concatenate_datasets
from huggingface_hub import snapshot_download
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from torch.utils.data import DataLoader, TensorDataset

DATASET_REVISION = "1b3a88a440f4922edfcd1e230e42c003c0e13bdf"
BACKBONE_REVISION = "bc7d5ab40c02133386a28e2c127f35c17c86901d"
SEED = 42
EPOCHS = 35
BATCH_SIZE = 64
LEARNING_RATE = 1e-4
CHECKPOINT_NAME = "mantis_pretrained_rca.pt"
STATS_NAME = "rca_preprocessing.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_split(dataset_dir: Path, scratch: Path):
    shards = sorted(dataset_dir.glob("telecom_ts-full-*.arrow"))
    if len(shards) != 3:
        raise FileNotFoundError(f"expected three pinned TelecomTS Arrow shards in {dataset_dir}")
    dataset = concatenate_datasets([Dataset.from_file(str(path)) for path in shards])
    from ran_ml_service.model import KPI_CHANNELS

    if len(dataset) != 32000 or list(dataset[0]["KPIs"]) != KPI_CHANNELS:
        raise ValueError("TelecomTS dataset size or KPI order differs from the pinned reference")
    splits = dataset.train_test_split(
        test_size=0.2,
        seed=SEED,
        train_indices_cache_file_name=str(scratch / "train_indices.arrow"),
        test_indices_cache_file_name=str(scratch / "test_indices.arrow"),
    )
    return splits


def examples(split):
    from ran_ml_service.model import RCA_CLASSES

    rca, jamming = [], []
    for item in split:
        anomaly = item["anomalies"]
        if not anomaly["exists"]:
            continue
        if anomaly["type"] == "Jamming":
            jamming.append(item)
        elif anomaly["type"] in RCA_CLASSES:
            rca.append(item)
        else:
            raise ValueError(f"unexpected anomaly class: {anomaly['type']!r}")
    return rca, jamming


def window(item):
    from ran_ml_service.model import KPI_CHANNELS

    return [dict(zip(KPI_CHANNELS, row)) for row in zip(*(item["KPIs"][channel] for channel in KPI_CHANNELS))]


def evaluate(predictor, samples, stats, preprocessing):
    """Evaluate through the same public predictor path used by /v1/classify."""
    from ran_ml_service.model import RCA_CLASSES

    if stats == "test":
        original_means, original_stds = predictor.rca_means, predictor.rca_stds
        predictor.rca_means = torch.tensor(preprocessing["test"]["means"]).view(1, 1, -1)
        predictor.rca_stds = torch.tensor(preprocessing["test"]["stds"]).view(1, 1, -1)
    try:
        predictions = [predictor.predict(window(item)) for item in samples]
    finally:
        if stats == "test":
            predictor.rca_means, predictor.rca_stds = original_means, original_stds
    actual = [RCA_CLASSES.index(item["anomalies"]["type"]) for item in samples]
    predicted = [result["class_index"] for result in predictions]
    return {
        "sample_count": len(samples),
        "accuracy": float(accuracy_score(actual, predicted)),
        "macro_f1": float(f1_score(actual, predicted, labels=list(range(10)), average="macro", zero_division=0)),
        "per_class": classification_report(actual, predicted, labels=list(range(10)), target_names=list(RCA_CLASSES), output_dict=True, zero_division=0),
        "confusion_matrix": confusion_matrix(actual, predicted, labels=list(range(10))).tolist(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="private durable directory for checkpoint and report")
    parser.add_argument("--dataset-dir", type=Path, default=Path.home() / ".cache/huggingface/datasets/AliMaatouk___telecom_ts/default-4915e1ea38a2f004/0.0.0" / DATASET_REVISION)
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("--epochs must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(args.output_dir, 0o700)

    # Keep the backbone revision fixed even when the Hub's default branch moves.
    backbone_path = snapshot_download("paris-noah/Mantis-8M", revision=BACKBONE_REVISION)
    os.environ["MANTIS_CHECKPOINT"] = backbone_path
    os.environ["TASK"] = "classify"
    from ran_ml_service.model import MantisPredictor, PretrainedMantisEncoder, RCA_CLASSES
    import ran_ml_service.model as model_module

    # The predictor config may have been imported before the environment changed.
    model_module.MANTIS_CHECKPOINT = backbone_path
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(min(torch.get_num_threads(), 8))
    scratch = args.output_dir / "split-cache"
    scratch.mkdir(exist_ok=True)
    splits = load_split(args.dataset_dir, scratch)
    train, _ = examples(splits["train"])
    test, jamming = examples(splits["test"])
    stats_path = Path(__file__).resolve().parents[1] / "artifacts" / STATS_NAME
    predictor_stats = json.loads(stats_path.read_text())
    if (len(train), len(test), len(jamming)) != (764, 192, 58):
        raise ValueError("seeded TelecomTS RCA split differs from the pinned reference")
    if predictor_stats["dataset_revision"] != DATASET_REVISION or predictor_stats["split_seed"] != SEED:
        raise ValueError("preprocessing artifact does not match the pinned split")

    # Check that the retained statistics really came from this training split.
    prep = MantisPredictor()
    raw = torch.cat([prep.preprocess(window(item)) for item in train])
    # Match the export script's [sample, channel, timestep] float32 reduction.
    raw_by_channel = raw.numpy().transpose(0, 2, 1).copy()
    measured_means = raw_by_channel.mean(axis=(0, 2))
    measured_stds = raw_by_channel.std(axis=(0, 2))
    if not np.array_equal(measured_means, predictor_stats["train"]["means"]) or not np.array_equal(measured_stds, predictor_stats["train"]["stds"]):
        raise ValueError("saved training statistics differ from the pinned split")
    prep.task = "root-cause analysis"
    prep.rca_means = torch.tensor(predictor_stats["train"]["means"]).view(1, 1, -1)
    prep.rca_stds = torch.tensor(predictor_stats["train"]["stds"]).view(1, 1, -1)
    x = (raw - prep.rca_means) / prep.rca_stds
    y = torch.tensor([RCA_CLASSES.index(item["anomalies"]["type"]) for item in train], dtype=torch.long)
    encoder = PretrainedMantisEncoder(checkpoint=backbone_path, d_model=64)
    head = nn.Sequential(nn.LayerNorm(64), nn.Dropout(0.2), nn.Linear(64, 10))
    optimizer = torch.optim.Adam(list(encoder.parameters()) + list(head.parameters()), lr=LEARNING_RATE)
    loader = DataLoader(TensorDataset(x, y), batch_size=BATCH_SIZE, shuffle=True)
    losses = []
    for epoch in range(args.epochs):
        encoder.train()
        head.train()
        running = []
        for batch_x, batch_y in loader:
            optimizer.zero_grad()
            loss = nn.functional.cross_entropy(head(encoder(batch_x)), batch_y)
            loss.backward()
            optimizer.step()
            running.append(float(loss.item()))
        losses.append(float(np.mean(running)))
        print(f"epoch {epoch + 1}/{args.epochs}: loss={losses[-1]:.5f}", flush=True)

    checkpoint_path = args.output_dir / CHECKPOINT_NAME
    torch.save({"encoder": encoder.cpu().state_dict(), "head": head.cpu().state_dict(), "task": "root-cause analysis", "n_classes": 10, "d_model": 64, "checkpoint": "paris-noah/Mantis-8M"}, checkpoint_path)
    shutil.copyfile(stats_path, args.output_dir / STATS_NAME)
    os.chmod(checkpoint_path, 0o600)
    os.chmod(args.output_dir / STATS_NAME, 0o600)
    model_module.TASK = "classify"
    model_module.MANTIS_MODEL_PATH = str(checkpoint_path)
    predictor = MantisPredictor()
    predictor.load()
    serving = evaluate(predictor, test, "train", predictor_stats)
    historical = evaluate(predictor, test, "test", predictor_stats)
    unsupported = [predictor.predict(window(item)) for item in jamming]
    confidences = [result["confidence"] for result in unsupported]
    thresholds_met = serving["accuracy"] >= 0.90 and serving["macro_f1"] >= 0.90
    passed = args.epochs == EPOCHS and thresholds_met
    report = {
        "status": "validated" if passed else "exploratory" if args.epochs != EPOCHS else "failed_gate",
        "gate": {"accuracy": 0.90, "macro_f1": 0.90, "thresholds_met": thresholds_met, "passed": passed},
        "serving_style_train_statistics": serving,
        "historical_test_statistics": historical,
        "jamming_unsupported": {"sample_count": len(jamming), "predicted_classes": dict(Counter(result["label"] for result in unsupported)), "confidence": {"min": min(confidences), "median": float(np.median(confidences)), "mean": float(np.mean(confidences)), "max": max(confidences)}},
        "provenance": {
            "dataset": "AliMaatouk/TelecomTS", "dataset_revision": DATASET_REVISION,
            "backbone": "paris-noah/Mantis-8M", "backbone_revision": BACKBONE_REVISION,
            "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "training_script_sha256": sha256(Path(__file__)),
            "split": {"test_size": 0.2, "seed": SEED, "train_rca": len(train), "test_rca": len(test), "test_jamming": len(jamming), "train_indices_sha256": sha256(scratch / "train_indices.arrow"), "test_indices_sha256": sha256(scratch / "test_indices.arrow")},
            "hyperparameters": {"epochs": args.epochs, "batch_size": BATCH_SIZE, "learning_rate": LEARNING_RATE, "projection_dim": 64, "encoder_dropout": 0.1, "head_dropout": 0.2, "optimizer": "Adam", "adam_betas": [0.9, 0.999], "adam_weight_decay": 0, "loss": "unweighted cross entropy", "input_timesteps": 128, "backbone_timesteps": 512},
            "classes": list(RCA_CLASSES), "epoch_losses": losses,
            "dependencies": {name: importlib.metadata.version(name) for name in ("torch", "numpy", "datasets", "scikit-learn", "huggingface-hub", "mantis-tsfm")},
        },
        "artifacts": {name: {"path": str(args.output_dir / name), "sha256": sha256(args.output_dir / name)} for name in (CHECKPOINT_NAME, STATS_NAME)},
    }
    report_path = args.output_dir / "rca_validation_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    os.chmod(report_path, 0o600)
    shutil.rmtree(scratch)
    print(f"{report['status']}: accuracy={serving['accuracy']:.4f}, macro_f1={serving['macro_f1']:.4f}; report={report_path}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
