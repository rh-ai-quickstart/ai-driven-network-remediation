import os
import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

FailureType = Literal[
    "OOMKilled",
    "CrashLoopBackOff",
    "ConfigError",
    "NetworkTimeout",
    "StorageFull",
    "CertificateExpired",
    "DNSFailure",
    "KafkaLag",
    "PostgresConnPool",
    "AAPJobFailure",
    "CPUThrottling",  # CPU misconfiguration demo.
    "Unknown",
]


class LogEvent(BaseModel):
    timestamp: str
    message: str
    level: str
    namespace: str
    pod_name: str
    container: str
    edge_site_id: str
    kafka_offset: int
    raw: str


class RemediationTarget(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    edge_site_id: str
    namespace: str = ""
    cluster_scoped: bool = Field(
        default=False,
        description="True only when investigation evidence establishes that the resource is cluster-scoped",
    )
    kind: str
    name: str
    container: str = ""

    @field_validator("namespace")
    @classmethod
    def _strip_namespace(cls, value: str) -> str:
        return value.strip()

    @field_validator("edge_site_id", "kind", "name")
    @classmethod
    def _require_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Target identity must not be blank")
        return value


class SelectedAction(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    goal: str = Field(description="One-sentence remediation instruction beginning with an action")
    target: RemediationTarget
    evidence: list[str] = Field(
        min_length=1, description="Exact supporting quotes from observations, not model conclusions"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Evidence-backed values needed for remediation"
    )
    verification: list[str] = Field(default_factory=list, description="Optional legacy remediation postconditions")

    @field_validator("goal")
    @classmethod
    def _require_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Remediation goal must not be blank")
        return value

    @field_validator("evidence", "verification")
    @classmethod
    def _require_observations(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Evidence and verification entries must not be blank")
        return values


class RootCauseAnalysis(BaseModel):
    failure_type: FailureType = Field(description="Category of the failure")
    confidence: float = Field(ge=0, le=1, description="Confidence score between 0.0 and 1.0")
    summary: str = Field(description="One-sentence human-readable summary of the root cause")
    evidence: list[str] = Field(description="List of evidence strings supporting the diagnosis")
    recommended_actions: list[str] = Field(description="Short executable remediation action names (not shell commands)")
    estimated_severity: Literal["critical", "high", "medium", "low"] = Field(description="Severity level")
    runbook_reference: str = Field(description="Runbook name or URL, or 'n/a' if none applies")
    selected_action: Optional[SelectedAction] = None

    @field_validator("evidence", "recommended_actions", mode="before")
    @classmethod
    def _coerce_to_list(cls, v):
        if isinstance(v, str):
            return [v]
        return v


class RemediationResult(BaseModel):
    action_taken: str
    tool_used: str
    success: bool
    job_id: str
    duration_seconds: float
    output_summary: str
    timestamp: str
    timed_out: bool = False
    lightspeed_conversation_id: Optional[str] = None
    generated_template_name: Optional[str] = None
    generated_template_id: Optional[str] = None
    generated_playbook_name: Optional[str] = None
    generated_playbook_preview: Optional[str] = None


class GraphConfig(BaseModel):
    remediate_threshold: float = 0.8
    escalate_threshold: float = 0.7
    # On a NO_MATCH from the playbook guard, generate a bespoke playbook via
    # lightspeed only when the agent is this confident about the root cause;
    # otherwise escalate to a human.
    lightspeed_threshold: float = 0.9
    enable_match_guard: bool = True
    job_timeout: float = 120.0
    tool_call_timeout: int = 10
    investigate_timeout: int = 120
    investigate_max_iterations: int = 12

    @model_validator(mode="before")
    @classmethod
    def _env_defaults(cls, values):
        if "investigate_max_iterations" not in values:
            env_val = os.getenv("INVESTIGATE_MAX_ITERATIONS")
            if env_val is not None:
                values["investigate_max_iterations"] = int(env_val)
        if "lightspeed_threshold" not in values:
            env_val = os.getenv("LIGHTSPEED_THRESHOLD")
            if env_val is not None:
                values["lightspeed_threshold"] = float(env_val)
        if "enable_match_guard" not in values:
            env_val = os.getenv("ENABLE_MATCH_GUARD")
            if env_val is not None:
                values["enable_match_guard"] = env_val.strip().lower() in {"1", "true", "yes", "on"}
        return values


class IncidentState(BaseModel):
    raw_event: str
    kafka_offset: int = 0
    log_event: Optional[LogEvent] = None
    incident_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    incident_start_ms: float = Field(default_factory=lambda: time.time() * 1000)
    context_snippets: list[str] = []
    rag_query_used: str = ""
    root_cause_analysis: Optional[RootCauseAnalysis] = None
    analysis_tokens_used: int = 0
    analysis_latency_ms: float = 0.0
    decision: str = ""
    selected_template: Optional[str] = None
    remediation_result: Optional[RemediationResult] = None
    pod_status: dict = {}
    cluster_events: list[dict] = []
    pod_logs: str = ""
    resource_specs: str = ""
    deployment_observations: list[dict] = []
    log_search_results: list[dict] = []
    recent_errors: list[dict] = []
    slack_thread_ts: str = ""
    servicenow_ticket: str = ""
    fast_path_actuation: str = ""
    total_duration_ms: float = 0.0
    error_message: str = ""
