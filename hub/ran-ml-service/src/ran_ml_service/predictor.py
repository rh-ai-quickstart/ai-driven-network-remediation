"""Mantis classify predictor — loads a fine-tuned encoder + head from a local
checkpoint or MLflow artifact and runs inference on TelecomTS kpi_window inputs.

The predictor is self-contained: normalization, interpolation, tensor
construction, and inference all happen here so callers only need to provide
the raw 128×18 kpi_window.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F
from loguru import logger
from telco_oran.domain.rca_classes import RCA_CLASSES

EXPECTED_TIMESTEPS = 128
EXPECTED_CHANNELS = 18
PRETRAINED_SEQ_LEN = 512


class ClassifyPredictor:
    def __init__(self) -> None:
        self._encoder: torch.nn.Module | None = None
        self._head: torch.nn.Module | None = None
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def load_local(self, path: str) -> None:
        """Load encoder + head from a local .pt checkpoint."""
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        d_model = ckpt.get("d_model", 64)
        n_classes = ckpt.get("n_classes", len(RCA_CLASSES))

        from mantis.architecture import MantisV1

        class _Encoder(torch.nn.Module):
            def __init__(self, checkpoint: str) -> None:
                super().__init__()
                self.backbone = MantisV1(device="cpu").from_pretrained(checkpoint)
                self.act = F.gelu
                self.dropout = torch.nn.Dropout(0.1)
                self.projection = torch.nn.Linear(256, d_model)

            def forward(self, x_enc):
                x = x_enc.transpose(1, 2).contiguous()
                B, C, T = x.shape
                x = x.reshape(B * C, 1, T)
                if T != PRETRAINED_SEQ_LEN:
                    x = F.interpolate(x, size=PRETRAINED_SEQ_LEN, mode="linear", align_corners=False)
                h = self.backbone(x).reshape(B, C, -1).mean(dim=1)
                return self.projection(self.dropout(self.act(h)))

        encoder = _Encoder(ckpt.get("checkpoint", "paris-noah/Mantis-8M"))
        encoder.load_state_dict(ckpt["encoder"])
        encoder.eval()

        head = torch.nn.Sequential(
            torch.nn.LayerNorm(d_model),
            torch.nn.Dropout(0.2),
            torch.nn.Linear(d_model, n_classes),
        )
        head.load_state_dict(ckpt["head"])
        head.eval()

        self._encoder = encoder
        self._head = head
        self._ready = True
        logger.info("Classify model loaded from {}", path)

    def load_mlflow(self, model_uri: str) -> None:
        """Load a model registered in MLflow."""
        import mlflow.pyfunc

        self._mlflow_model = mlflow.pyfunc.load_model(model_uri)
        self._ready = True
        logger.info("Classify model loaded from MLflow: {}", model_uri)

    def predict(self, kpi_window: list[list[float]]) -> dict:
        """Classify a single kpi_window (128×18) and return the result."""
        arr = np.array(kpi_window, dtype=np.float32)
        if arr.shape != (EXPECTED_TIMESTEPS, EXPECTED_CHANNELS):
            raise ValueError(
                f"kpi_window must be {EXPECTED_TIMESTEPS}×{EXPECTED_CHANNELS}, "
                f"got {arr.shape}"
            )

        arr = self._normalize(arr)

        if hasattr(self, "_mlflow_model"):
            flat = arr.reshape(1, -1)
            preds = self._mlflow_model.predict(flat)
            class_index = int(preds[0])
            return {
                "class": RCA_CLASSES[class_index],
                "confidence": 1.0,
                "class_index": class_index,
            }

        x = torch.tensor(arr, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            logits = self._head(self._encoder(x.permute(0, 2, 1)))
            probs = F.softmax(logits, dim=1)
            confidence, class_index = probs.max(dim=1)

        idx = int(class_index.item())
        return {
            "class": RCA_CLASSES[idx],
            "confidence": float(confidence.item()),
            "class_index": idx,
        }

    @staticmethod
    def _normalize(arr: np.ndarray) -> np.ndarray:
        """Per-channel z-score normalization."""
        mean = arr.mean(axis=0, keepdims=True)
        std = arr.std(axis=0, keepdims=True)
        std[std == 0] = 1.0
        return (arr - mean) / std
