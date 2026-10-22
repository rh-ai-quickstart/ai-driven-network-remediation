"""ran-ml-service configuration from environment variables."""

from __future__ import annotations

import os

TASK = os.getenv("TASK", "detect")

MANTIS_MODEL_PATH = os.getenv("MANTIS_MODEL_PATH", "")

MANTIS_CHECKPOINT = os.getenv("MANTIS_CHECKPOINT", "paris-noah/Mantis-8M")

PORT = int(os.getenv("PORT", "8080"))
