import os
import time
import uuid
from typing import Literal, NotRequired, Optional, TypedDict

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


class RootCauseAnalysis(BaseModel):
    failure_type: FailureType = Field(description="Category of the failure")
    confidence: float = Field(ge=0, le=1, description="Confidence score between 0.0 and 1.0")
    summary: str = Field(description="One-sentence human-readable summary of the root cause")
    evidence: list[str] = Field(description="List of evidence strings supporting the diagnosis")
    recommended_actions: list[str] = Field(
        description="Short executable remediation action names (not shell commands)"
    )
    estimated_severity: Literal["critical", "high", "medium", "low"] = Field(
        description="Severity level"
    )
    runbook_reference: str = Field(description="Runbook name or URL, or 'n/a' if none applies")

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
    generated_template_name: Optional[str] = None
    generated_template_id: Optional[str] = None
    generated_playbook_name: Optional[str] = None
    generated_playbook_preview: Optional[str] = None


class FailedAttempt(TypedDict):
    action: str
    template: str
    error: str
    job_id: NotRequired[int]


class GraphConfig(BaseModel):
    remediate_threshold: float = 0.8
    escalate_threshold: float = 0.7
    max_retries: int = 1
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
        return values


class IncidentState(BaseModel):
    raw_event: str
    kafka_offset: int = 0
    log_event: Optional[LogEvent] = None
    incident_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    incident_start_ms: float = Field(default_factory=lambda: time.time() * 1000)
    confidence_override: Optional[float] = None
    failure_type_override: Optional[FailureType] = None
    context_snippets: list[str] = []
    rag_query_used: str = ""
    root_cause_analysis: Optional[RootCauseAnalysis] = None
    analysis_tokens_used: int = 0
    analysis_latency_ms: float = 0.0
    decision: str = ""
    failed_attempts: list[FailedAttempt] = []
    should_retry: bool = False
    remediation_result: Optional[RemediationResult] = None
    pod_status: dict = {}
    cluster_events: list[dict] = []
    pod_logs: str = ""
    resource_specs: str = ""
    log_search_results: list[dict] = []
    recent_errors: list[dict] = []
    slack_thread_ts: str = ""
    servicenow_ticket: str = ""
    langfuse_trace_id: str = ""
    fast_path_actuation: str = ""
    total_duration_ms: float = 0.0
    error_message: str = ""
