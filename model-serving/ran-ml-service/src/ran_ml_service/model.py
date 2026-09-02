"""Mantis pretrained encoder + classification head for TelecomTS.

Replicates the architecture from the training notebook (PR #127):
- PretrainedMantisEncoder: per-channel MantisV1 backbone + mean-pooling + projection
- Classification head: LayerNorm -> Linear (AD: 2 classes, RCA: 10 classes)

The model loads pretrained backbone weights from HuggingFace (paris-noah/Mantis-8M)
and fine-tuned task weights from a local .pt checkpoint.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from loguru import logger

from .config import MANTIS_CHECKPOINT, MANTIS_MODEL_PATH, TASK

PRETRAINED_SEQ_LEN = 512
PRETRAINED_HIDDEN_DIM = 256

KPI_CHANNELS = [
    "RSRP", "DL_BLER", "DL_MCS", "UL_BLER", "UL_MCS", "UL_NPRB",
    "UL_SNR", "TX_Bytes", "RX_Bytes", "Estimated_UL_Buffer",
    "PRBs_DL_Current", "PRBs_UL_Current", "PRB_Utilization_DL",
    "PRB_Utilization_UL", "UL_Protocol", "UL_NumberOfPackets",
    "DL_Protocol", "DL_NumberOfPackets",
]

PROTOCOL_MAP = {"TCP": 0, "UDP": 1, None: 2, "None": 2}

AD_LABELS = ["normal", "anomalous"]
RCA_LABELS = [
    "Antenna Failure",
    "Co-Channel Interference (Mild)",
    "Co-Channel Interference (Severe)",
    "Faulty RF Filters (Temporal)",
    "Doppler Shift (Severe)",
    "Faulty Handover Algorithm (Too Frequent)",
    "Buffer Overflow (Gradual Buildup)",
    "Resource Allocation Bugs",
    "High Network Congestion (Gradual Buildup)",
    "High Network Congestion (Sudden Spike)",
]


class PretrainedMantisEncoder(nn.Module):
    """Mantis-V1 with pretrained backbone (paris-noah/Mantis-8M).

    Same per-channel processing and mean-pooling as the TelecomTS wrapper,
    but starts from pretrained weights (8.1M params, hidden_dim=256).
    """

    def __init__(self, checkpoint: str = MANTIS_CHECKPOINT, d_model: int = 64, dropout: float = 0.1):
        super().__init__()
        from mantis.architecture import MantisV1

        self.backbone = MantisV1(device="cpu")
        self.backbone = self.backbone.from_pretrained(checkpoint)
        self.act = F.gelu
        self.dropout = nn.Dropout(dropout)
        self.projection = nn.Linear(PRETRAINED_HIDDEN_DIM, d_model)

    def forward(self, x_enc: torch.Tensor) -> torch.Tensor:
        """x_enc: [B, T, C] -> [B, d_model]"""
        x = x_enc.transpose(1, 2).contiguous()  # [B, C, T]
        B, C, T = x.shape
        x = x.reshape(B * C, 1, T)
        if T != PRETRAINED_SEQ_LEN:
            x = F.interpolate(x, size=PRETRAINED_SEQ_LEN, mode="linear", align_corners=False)
        h = self.backbone(x)  # [B*C, 256]
        h = h.reshape(B, C, -1)  # [B, C, 256]
        h = h.mean(dim=1)  # [B, 256]
        h = self.dropout(self.act(h))
        return self.projection(h)  # [B, d_model]


class MantisPredictor:
    """Wraps encoder + head for inference. Handles loading and preprocessing."""

    def __init__(self):
        self.encoder: PretrainedMantisEncoder | None = None
        self.head: nn.Module | None = None
        self.task: str = ""
        self.n_classes: int = 0
        self.labels: list[str] = []
        self._loaded = False

    @property
    def is_ready(self) -> bool:
        return self._loaded

    def load(self) -> None:
        """Load model weights from MANTIS_MODEL_PATH."""
        weights_path = self._resolve_weights_path()
        if weights_path is None:
            logger.warning("No model weights configured (MANTIS_MODEL_PATH empty)")
            return

        logger.info("Loading model weights from: {}", weights_path)
        ckpt = torch.load(weights_path, map_location="cpu", weights_only=False)

        task = ckpt.get("task", "anomaly detection")
        n_classes = ckpt.get("n_classes", 2)
        d_model = ckpt.get("d_model", 64)
        checkpoint = ckpt.get("checkpoint", MANTIS_CHECKPOINT)

        self.encoder = PretrainedMantisEncoder(checkpoint=checkpoint, d_model=d_model)
        self.encoder.load_state_dict(ckpt["encoder"])
        self.encoder.eval()

        if task == "anomaly detection":
            self.head = nn.Sequential(nn.LayerNorm(d_model), nn.Linear(d_model, n_classes))
        else:
            self.head = nn.Sequential(nn.LayerNorm(d_model), nn.Dropout(0.2), nn.Linear(d_model, n_classes))
        self.head.load_state_dict(ckpt["head"])
        self.head.eval()

        self.task = task
        self.n_classes = n_classes
        self.labels = AD_LABELS if task == "anomaly detection" else RCA_LABELS

        self._loaded = True
        logger.info("Model loaded: task={}, n_classes={}, d_model={}", task, n_classes, d_model)

    def _resolve_weights_path(self) -> str | None:
        if MANTIS_MODEL_PATH:
            path = Path(MANTIS_MODEL_PATH)
            if path.exists():
                return str(path)
            logger.error("MANTIS_MODEL_PATH does not exist: {}", MANTIS_MODEL_PATH)
            return None

        return None

    def preprocess(self, kpi_window: list[dict]) -> torch.Tensor:
        """Convert JSON kpi_window (128 timesteps x 18 channels) to model input tensor.

        AD preprocessing: Protocol encoding only, NO z-score normalization.
        Returns tensor of shape [1, 128, 18].
        """
        rows = []
        for timestep in kpi_window:
            row = []
            for ch in KPI_CHANNELS:
                val = timestep.get(ch, 0)
                if ch in ("UL_Protocol", "DL_Protocol"):
                    val = PROTOCOL_MAP.get(val, val) if isinstance(val, str) else float(val)
                row.append(float(val))
            rows.append(row)

        x = np.array(rows, dtype=np.float32)  # [128, 18]
        return torch.tensor(x, dtype=torch.float32).unsqueeze(0)  # [1, 128, 18]

    def predict(self, kpi_window: list[dict]) -> dict:
        """Run inference on a single kpi_window. Returns label + confidence."""
        if not self._loaded:
            raise RuntimeError("Model not loaded")

        x = self.preprocess(kpi_window)
        with torch.no_grad():
            embedding = self.encoder(x)
            logits = self.head(embedding)
            probs = F.softmax(logits, dim=1)
            pred_idx = probs.argmax(dim=1).item()
            confidence = probs[0, pred_idx].item()

        return {
            "label": self.labels[pred_idx],
            "confidence": round(confidence, 4),
            "class_index": pred_idx,
        }


predictor = MantisPredictor()
