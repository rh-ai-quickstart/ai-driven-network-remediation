from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RemediationEvent(BaseModel):
    failure_type: str = Field(..., examples=["OOMKilled"])
    namespace: str
    deployment: str
    site_id: str
    pod: str | None = None
    reason: str | None = None


class RemediationResult(BaseModel):
    site_id: str
    namespace: str
    deployment: str
    action: Literal["local_fast_path_restart"] = "local_fast_path_restart"
    result: Literal["success", "skipped", "failed"]
    timestamp: datetime
    message: str = ""
