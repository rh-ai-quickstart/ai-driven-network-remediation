"""ran-ml-service configuration from environment variables."""

from __future__ import annotations

import os

TASK = os.getenv("TASK", "classify")

MANTIS_MODEL_PATH = os.getenv("MANTIS_MODEL_PATH", "")
MLFLOW_MODEL_URI = os.getenv("MLFLOW_MODEL_URI", "")
