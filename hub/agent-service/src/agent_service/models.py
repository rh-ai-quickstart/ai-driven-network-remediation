import time
import uuid
from typing import Literal, NotRequired, Optional, TypedDict

from pydantic import BaseModel, Field

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
    failure_type: FailureType
    confidence: float
    summary: str
    evidence: list[str]
    recommended_actions: list[str]
    estimated_severity: Literal["critical", "high", "medium", "low"]
    runbook_reference: str


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
    investigate_timeout: int = 45
    investigate_max_iterations: int = 3


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
    log_search_results: list[dict] = []
    recent_errors: list[dict] = []
    slack_thread_ts: str = ""
    servicenow_ticket: str = ""
    langfuse_trace_id: str = ""
    total_duration_ms: float = 0.0
    error_message: str = ""
