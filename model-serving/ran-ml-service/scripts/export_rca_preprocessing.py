"""Export fixed RCA channel statistics from the notebook's seeded training split.

The TelecomTS reference normalizes the whole RCA train and test splits
independently. A service cannot normalize across incoming windows without
changing predictions based on request traffic, so production uses the train
split statistics. The test statistics are recorded for notebook parity.
"""

import hashlib
import json
import os
from pathlib import Path

import numpy as np
from datasets import Dataset, concatenate_datasets

from ran_ml_service.model import KPI_CHANNELS, PROTOCOL_MAP


def main() -> None:
    cache = Path(os.environ.get("TELECOMTS_ARROW_DIR", "")) if os.environ.get("TELECOMTS_ARROW_DIR") else Path.home() / ".cache/huggingface/datasets/AliMaatouk___telecom_ts/default-4915e1ea38a2f004/0.0.0/1b3a88a440f4922edfcd1e230e42c003c0e13bdf"
    shards = sorted(cache.glob("telecom_ts-full-*.arrow"))
    if len(shards) != 3:
        raise FileNotFoundError("The pinned TelecomTS cached Arrow shards are required")
    dataset = concatenate_datasets([Dataset.from_file(str(path)) for path in shards])
    if list(dataset[0]["KPIs"]) != KPI_CHANNELS:
        raise ValueError("cached TelecomTS feature order differs from the notebook")
    splits = dataset.train_test_split(
        test_size=0.2, seed=42,
        train_indices_cache_file_name="/tmp/telecomts_rca_train_indices.arrow",
        test_indices_cache_file_name="/tmp/telecomts_rca_test_indices.arrow",
    )
    output = {
        "dataset": "AliMaatouk/TelecomTS",
        "dataset_revision": "1b3a88a440f4922edfcd1e230e42c003c0e13bdf",
        "split_seed": 42,
        "channels": KPI_CHANNELS,
        "protocol_map": {"TCP": 0, "UDP": 1, "None": 2},
        "normalization": "per-channel mean and population std over all RCA examples and 128 timesteps in the split",
    }
    for name in ("train", "test"):
        arrays = []
        for item in splits[name]:
            if not item["anomalies"]["exists"] or item["anomalies"]["type"] == "Jamming":
                continue
            arrays.append(np.stack([
                np.asarray([PROTOCOL_MAP.get(v, 2) for v in item["KPIs"][channel]], dtype=np.float32)
                if channel in ("UL_Protocol", "DL_Protocol")
                else np.asarray(item["KPIs"][channel], dtype=np.float32)
                for channel in KPI_CHANNELS
            ]))
        values = np.stack(arrays)
        means = values.mean(axis=(0, 2), keepdims=True)
        stds = values.std(axis=(0, 2), keepdims=True)
        stds[stds == 0] = 1
        output[name] = {"examples": len(arrays), "means": means.reshape(-1).tolist(), "stds": stds.reshape(-1).tolist()}
    destination = Path(__file__).resolve().parents[1] / "artifacts/rca_preprocessing.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2) + "\n")
    print(destination, hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
