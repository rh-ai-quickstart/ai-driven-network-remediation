import json
from copy import deepcopy

from agent_service.models import (
    IncidentState,
    LogEvent,
    RemediationTarget,
    RootCauseAnalysis,
    SelectedAction,
)

CANONICAL_RAW_EVENT = json.dumps(
    {
        "@timestamp": "2024-01-01T00:00:00Z",
        "message": "CrashLoopBackOff",
        "level": "error",
        "kubernetes": {
            "namespace_name": "prod",
            "pod_name": "nginx-abc123",
            "container_name": "nginx",
        },
        "labels": {
            "edge_site_id": "edge-1",
        },
    }
)


def make_log_event(**overrides):
    defaults = dict(
        timestamp="2024-01-01T00:00:00Z",
        message="CrashLoopBackOff",
        level="error",
        namespace="prod",
        pod_name="nginx-abc123",
        container="nginx",
        edge_site_id="edge-1",
        kafka_offset=42,
        raw=CANONICAL_RAW_EVENT,
    )
    defaults.update(overrides)
    return LogEvent(**defaults)


def make_rca(**overrides):
    defaults = dict(
        failure_type="OOMKilled",
        confidence=0.92,
        summary="Container killed by OOM",
        evidence=["memory spike at 14:32"],
        recommended_actions=["increase memory limit"],
        estimated_severity="high",
        runbook_reference="runbook-oom-001",
    )
    defaults.update(overrides)
    return RootCauseAnalysis(**defaults)


def make_state(**overrides):
    defaults = dict(raw_event=CANONICAL_RAW_EVENT, log_event=make_log_event())
    defaults.update(overrides)
    return IncidentState(**defaults)


RESTART_AT = "2026-11-02T10:00:00Z"
RESTART_EVIDENCE = "Deployment orders-api workers are stuck; restart is required after the dependency recovered"
RESTART_VERIFICATION = "The Deployment completes its rollout with all desired replicas available"


def selected_state(site="edge-1", namespace="prod", deployment="orders-api", **overrides):
    target = RemediationTarget(edge_site_id=site, namespace=namespace, kind="Deployment", name=deployment)
    evidence = RESTART_EVIDENCE.replace("orders-api", deployment)
    selected = SelectedAction(
        goal=f"Restart Deployment {deployment} after its dependency recovered and verify its rollout",
        target=target,
        evidence=[evidence],
        parameters={"restart_at": RESTART_AT},
        verification=[RESTART_VERIFICATION],
    )
    return make_state(
        **{
            "root_cause_analysis": make_rca(selected_action=selected),
            "pod_logs": evidence,
            **overrides,
        }
    )


def update_selection(state, target=None, **updates):
    selected = state.root_cause_analysis.selected_action
    if target:
        updates["target"] = selected.target.model_copy(update=target)
    state.root_cause_analysis.selected_action = selected.model_copy(update=updates)


_RESTART_PLAYBOOK = [
    {
        "name": "Restart selected Deployment",
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": [
            {
                "name": "Restart the selected workload",
                "kubernetes.core.k8s": {
                    "state": "present",
                    "wait": True,
                    "definition": {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {"name": "{{ deployment_name }}", "namespace": "{{ namespace }}"},
                        "spec": {
                            "template": {
                                "metadata": {
                                    "annotations": {
                                        "kubectl.kubernetes.io/restartedAt": "{{ remediation_parameters.restart_at }}"
                                    }
                                }
                            }
                        },
                    },
                },
            }
        ],
    }
]


def restart_playbook():
    """Independent generated output; no production helper builds this fixture."""
    return deepcopy(_RESTART_PLAYBOOK)
