from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RootCauseAnalysis(BaseModel):
    root_cause: str
    confidence: float
    severity: Severity
    affected_components: list[str]
    recommended_playbook: str
    reasoning: str


class GraphConfig(BaseModel):
    remediate_threshold: float = 0.8
    escalate_threshold: float = 0.7


class RemediationState(BaseModel):
    raw_event: str
    confidence_override: Optional[float] = None
    context_snippets: list[str] = []
    root_cause_analysis: Optional[RootCauseAnalysis] = None
    decision: str = ""
    execution_result: str = ""
    notifications_sent: list[str] = []
    awaiting_human_approval: bool = False
