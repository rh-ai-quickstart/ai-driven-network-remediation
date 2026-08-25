import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from helpers import make_log_event, make_rca

from agent_service.models import (
    IncidentState,
    RemediationResult,
)
from agent_service.nodes.lightspeed import (
    _build_attachments,
    _build_playbook_name,
    _build_prompt,
    _extract_yaml,
    _summarize_evidence,
    lightspeed_node,
)
from agent_service.playbook_sanitize import (
    _APPLY_PATCH,
    _JSON_PATCH,
    _STRATEGIC_MERGE_PATCH,
    _fix_cluster_proxy_auth,
    _fix_patch_tasks,
    _is_dangerous_header,
    _iter_uri_tasks,
    _strip_dangerous_headers,
    fix_ansible_facts,
    quote_jinja,
    sanitize_playbook,
)

_ALS_RESPONSE = {
    "conversation_id": "conv-123",
    "response": "```yaml\n- hosts: all\n  tasks: []\n```",
    "referenced_documents": [],
    "truncated": False,
    "input_tokens": 100,
    "output_tokens": 50,
}

_UPSERT_OK = {
    "success": True,
    "template_id": 7,
    "created": True,
    "template_name": "remediate-oomkilled-nginx-abc123",
}

_LAUNCH_OK = {
    "success": True,
    "job_id": 42,
    "status": "pending",
    "template_name": "remediate-oomkilled-nginx-abc123",
}


def _state(rca=None, log_event=None, use_defaults=True, **extra):
    return IncidentState(
        raw_event="test event",
        root_cause_analysis=make_rca() if use_defaults and rca is None else rca,
        log_event=make_log_event() if use_defaults and log_event is None else log_event,
        **extra,
    )


_COMMIT_OK = {
    "success": True,
    "file_path": "playbooks/remediate-oomkilled-nginx-abc123.yaml",
    "sha": "abc123",
}

_SYNC_OK = {
    "success": True,
    "project_id": 1,
    "update_id": 99,
}

_SYNC_STATUS_OK = {
    "success": True,
    "update_id": 99,
    "status": "successful",
    "finished": "2026-07-28T12:00:00Z",
}


async def _default_invoke(tool_name, kwargs):
    if tool_name == "commit_playbook":
        return {
            "success": True,
            "file_path": f"playbooks/{kwargs['playbook_name']}.yaml",
            "sha": "abc123",
        }
    if tool_name == "sync_project":
        return _SYNC_OK
    if tool_name == "get_project_update_status":
        return _SYNC_STATUS_OK
    if tool_name == "upsert_job_template":
        return _UPSERT_OK
    if tool_name == "get_pod_spec":
        return {
            "success": True,
            "name": kwargs.get("name", ""),
            "namespace": kwargs.get("namespace", ""),
            "spec": {},
            "error": None,
        }
    if tool_name == "launch_job":
        return _LAUNCH_OK
    raise ValueError(f"Unexpected tool: {tool_name}")


async def _run_node(
    als_return=None,
    als_side_effect=None,
    invoke_fn=None,
    llm_summary=None,
    llm_summarize_side_effect=None,
    **state_kw,
):
    als_mock = AsyncMock(
        return_value=als_return,
        side_effect=als_side_effect,
    )
    if invoke_fn is None:
        invoke_fn = _default_invoke
    invoke_mock = AsyncMock(side_effect=invoke_fn)
    summarize_mock = AsyncMock(
        return_value=llm_summary or {},
        side_effect=llm_summarize_side_effect,
    )
    with (
        patch("agent_service.nodes.lightspeed.LIGHTSPEED_URL", "https://als-stub"),
        patch("agent_service.nodes.lightspeed._call_als", als_mock),
        patch("agent_service.nodes.lightspeed._invoke_tool", invoke_mock),
        patch("agent_service.nodes.lightspeed._summarize_evidence", summarize_mock),
    ):
        result = await lightspeed_node(_state(**state_kw))
    return result, als_mock, invoke_mock


# -- _build_playbook_name --


@pytest.mark.parametrize(
    "log_kw, expected_scope",
    [
        (dict(pod_name="nginx-abc"), "nginx-abc"),
        (dict(pod_name="", namespace="prod"), "prod"),
        (dict(pod_name="", namespace="", edge_site_id="edge-5"), "edge-5"),
        (dict(pod_name="", namespace="", edge_site_id=""), "cluster"),
    ],
)
def test_playbook_name_cascade(log_kw, expected_scope):
    assert _build_playbook_name(make_rca(), make_log_event(**log_kw)) == (f"remediate-oomkilled-{expected_scope}")


def test_playbook_name_no_rca():
    name = _build_playbook_name(None, make_log_event())
    assert name == "remediate-unknown-nginx-abc123"


def test_playbook_name_no_log():
    name = _build_playbook_name(make_rca(), None)
    assert name == "remediate-oomkilled-cluster"


# -- _extract_yaml --


@pytest.mark.parametrize(
    "input_text, expected",
    [
        ("```yaml\nkey: val\n```", "key: val"),
        ("```yml\nkey: val\n```", "key: val"),
        ("```\nkey: val\n```", "key: val"),
        ("key: val", "key: val"),
        ("", ""),
    ],
)
def test_extract_yaml_valid(input_text, expected):
    text, parsed = _extract_yaml(input_text)
    assert text == expected
    assert parsed is not None or input_text == ""


def test_extract_yaml_invalid_returns_raw():
    bad = "key: [unterminated"
    text, parsed = _extract_yaml(bad)
    assert text == bad
    assert parsed is None


def test_extract_yaml_unquoted_jinja_gets_quoted():
    raw = "- name: patch\n  hosts: localhost\n  tasks:\n  - name: do\n    uri:\n      url: {{ hub_url }}\n"
    text, parsed = _extract_yaml(raw)
    assert parsed is not None
    assert '"{{ hub_url }}"' in text


# -- _build_prompt --


def test_prompt_includes_rca_fields():
    prompt = _build_prompt(
        make_rca(
            failure_type="DNSFailure",
            estimated_severity="critical",
            summary="DNS fail",
            recommended_actions=["fix", "check"],
            evidence=["resolver timeout", "upstream unreachable"],
        ),
        make_log_event(namespace="kube-system", pod_name="coredns-1"),
    )
    for s in [
        "DNSFailure",
        "critical",
        "kube-system",
        "coredns-1",
        "DNS fail",
        "fix, check",
        "resolver timeout",
        "upstream unreachable",
    ]:
        assert s in prompt


def test_prompt_none_inputs():
    prompt = _build_prompt(None, None)
    assert "Unknown" in prompt
    assert "unknown" in prompt


def test_prompt_with_llm_summary_overrides_fields():
    llm_summary = {
        "affected_component": "orders-api-f8ynqtska4",
        "root_cause": "CPU throttling on orders-api-f8ynqtska4 causes upstream queue buildup",
        "remediation_steps": "increase CPU limits on orders-api-f8ynqtska4 deployment",
    }
    prompt = _build_prompt(
        make_rca(summary="queue growing", recommended_actions=["manual review"]),
        make_log_event(pod_name="web-frontend-abc123"),
        llm_summary=llm_summary,
    )
    assert "orders-api-f8ynqtska4" in prompt
    assert "CPU throttling" in prompt
    assert "increase CPU limits" in prompt
    assert "web-frontend-abc123" not in prompt
    assert "manual review" not in prompt


def test_prompt_with_empty_llm_summary_uses_rca():
    prompt = _build_prompt(
        make_rca(summary="OOM kill"),
        make_log_event(pod_name="nginx-abc"),
        llm_summary={},
    )
    assert "OOM kill" in prompt
    assert "nginx-abc" in prompt


# -- _build_attachments --


@pytest.mark.parametrize(
    "raw, evidence, expected_count",
    [
        ("raw log", ["ev1"], 2),
        ("", ["ev1"], 1),
        ("raw log", [], 1),
        ("", [], 0),
    ],
)
def test_attachments_count(raw, evidence, expected_count):
    atts = _build_attachments(make_rca(evidence=evidence), make_log_event(raw=raw))
    assert len(atts) == expected_count


def test_attachments_none_inputs():
    assert _build_attachments(None, None) == []


def test_attachments_content():
    atts = _build_attachments(make_rca(evidence=["a", "b"]), make_log_event(raw="log data"))
    assert atts[0] == {
        "attachment_type": "log",
        "content_type": "text/plain",
        "content": "log data",
    }
    assert atts[1]["content"] == "a\nb"


# -- lightspeed_node success --


class TestLightspeedNodeSuccess:
    async def test_returns_successful_result(self):
        result, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        assert result["decision"] == "lightspeed"
        rr = result["remediation_result"]
        assert isinstance(rr, RemediationResult)
        assert rr.success is True
        assert rr.job_id == "42"
        assert rr.generated_playbook_name == "remediate-oomkilled-nginx-abc123"
        assert "hosts: all" in rr.generated_playbook_preview
        assert "```" not in rr.generated_playbook_preview
        assert rr.duration_seconds >= 0
        # No retargeting in the default flow, so get_pod_spec is skipped:
        # commit, sync, poll-status, upsert, launch.
        assert invoke_mock.call_count == 5

    async def test_passes_prompt_and_attachments_to_als(self):
        _, mock, _ = await _run_node(als_return=_ALS_RESPONSE)
        prompt, attachments = mock.call_args[0]
        for expected in [
            "OOMKilled",
            "high",
            "prod",
            "nginx-abc123",
            "Container killed by OOM",
            "memory spike at 14:32",
            "increase memory limit",
        ]:
            assert expected in prompt, f"{expected!r} not found in prompt"
        assert isinstance(attachments, list)

    async def test_no_rca(self):
        result, _, _ = await _run_node(
            als_return=_ALS_RESPONSE,
            rca=None,
            use_defaults=False,
        )
        rr = result["remediation_result"]
        assert rr.success is True
        assert "unknown" in rr.generated_playbook_name

    async def test_no_log_event(self):
        result, _, _ = await _run_node(
            als_return=_ALS_RESPONSE,
            log_event=None,
            use_defaults=False,
        )
        name = result["remediation_result"].generated_playbook_name
        assert "cluster" in name

    async def test_empty_als_response_fails_closed(self):
        result, _, invoke_mock = await _run_node(als_return={"response": ""})
        rr = result["remediation_result"]
        assert rr.success is False
        assert "YAML validation" in rr.output_summary
        invoke_mock.assert_not_called()

    async def test_unparseable_yaml_fails_closed(self):
        result, _, invoke_mock = await _run_node(als_return={"response": "not: [valid: yaml: {{"})
        rr = result["remediation_result"]
        assert rr.success is False
        assert "YAML validation" in rr.output_summary
        invoke_mock.assert_not_called()


# -- AAP execution (commit + sync + upsert + launch) --


class TestAAPExecution:
    async def test_commit_playbook_called_first(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        first_call = invoke_mock.call_args_list[0]
        assert first_call[0][0] == "commit_playbook"
        args = first_call[0][1]
        assert args["playbook_name"] == "remediate-oomkilled-nginx-abc123"
        assert "hosts: all" in args["playbook_content"]

    async def test_sync_project_called_after_commit(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        second_call = invoke_mock.call_args_list[1]
        assert second_call[0][0] == "sync_project"

    async def test_upsert_uses_committed_file_path(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        upsert_call = [c for c in invoke_mock.call_args_list if c[0][0] == "upsert_job_template"][0]
        args = upsert_call[0][1]
        assert args["playbook"] == "playbooks/remediate-oomkilled-nginx-abc123.yaml"
        assert args["base_template_name"] == "lightspeed-runner"
        assert args["template_name"] == "remediate-oomkilled-nginx-abc123"

    async def test_launch_extra_vars_contain_runtime_context_only(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["namespace"] == "prod"
        assert extra_vars["pod_name"] == "nginx-abc123"
        assert extra_vars["edge_site_id"] == "edge-1"
        assert extra_vars["deployment_name"] == "nginx-abc123"
        assert "generated_playbook_yaml" not in extra_vars
        assert "generated_from_model" not in extra_vars

    async def test_commit_failure_skips_remaining_steps(self):
        async def commit_fails(tool_name, kwargs):
            if tool_name == "commit_playbook":
                return {"success": False, "error": "gitea unreachable"}
            return await _default_invoke(tool_name, kwargs)

        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=commit_fails,
        )

        rr = result["remediation_result"]
        assert rr.success is False
        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "sync_project" not in tool_names
        assert "upsert_job_template" not in tool_names
        assert "launch_job" not in tool_names

    async def test_sync_failure_skips_remaining_steps(self):
        async def sync_fails(tool_name, kwargs):
            if tool_name == "sync_project":
                return {"success": False, "error": "sync timed out"}
            return await _default_invoke(tool_name, kwargs)

        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=sync_fails,
        )

        rr = result["remediation_result"]
        assert rr.success is False
        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "launch_job" not in tool_names

    async def test_upsert_failure(self):
        async def upsert_fails(tool_name, kwargs):
            if tool_name == "upsert_job_template":
                return {"success": False, "error": "template conflict"}
            return await _default_invoke(tool_name, kwargs)

        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=upsert_fails,
        )

        rr = result["remediation_result"]
        assert rr.success is False
        assert rr.generated_playbook_name is not None
        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "launch_job" not in tool_names

    async def test_launch_failure(self):
        async def launch_fails(tool_name, kwargs):
            if tool_name == "launch_job":
                return {"success": False, "error": "quota exceeded"}
            return await _default_invoke(tool_name, kwargs)

        result, _, _ = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=launch_fails,
        )

        rr = result["remediation_result"]
        assert rr.success is False
        assert rr.generated_playbook_name is not None

    async def test_missing_file_path_from_commit(self):
        async def commit_no_path(tool_name, kwargs):
            if tool_name == "commit_playbook":
                return {"success": True, "sha": "abc"}
            return await _default_invoke(tool_name, kwargs)

        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=commit_no_path,
        )

        rr = result["remediation_result"]
        assert rr.success is False
        assert "playbook_path is required" in rr.output_summary
        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "launch_job" not in tool_names

    async def test_sync_poll_waits_for_successful(self):
        poll_count = 0

        async def sync_pending_then_ok(tool_name, kwargs):
            nonlocal poll_count
            if tool_name == "get_project_update_status":
                poll_count += 1
                if poll_count < 3:
                    return {"success": True, "update_id": 99, "status": "pending"}
                return _SYNC_STATUS_OK
            return await _default_invoke(tool_name, kwargs)

        with patch("agent_service.nodes.lightspeed._SYNC_POLL_INTERVAL", 0):
            result, _, invoke_mock = await _run_node(
                als_return=_ALS_RESPONSE,
                invoke_fn=sync_pending_then_ok,
            )

        assert result["remediation_result"].success is True
        assert poll_count == 3

    async def test_sync_poll_failure_stops_execution(self):
        async def sync_fails_status(tool_name, kwargs):
            if tool_name == "get_project_update_status":
                return {"success": True, "update_id": 99, "status": "failed"}
            return await _default_invoke(tool_name, kwargs)

        with patch("agent_service.nodes.lightspeed._SYNC_POLL_INTERVAL", 0):
            result, _, invoke_mock = await _run_node(
                als_return=_ALS_RESPONSE,
                invoke_fn=sync_fails_status,
            )

        rr = result["remediation_result"]
        assert rr.success is False
        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "upsert_job_template" not in tool_names
        assert "launch_job" not in tool_names

    async def test_sync_poll_timeout_stops_execution(self):
        async def sync_always_pending(tool_name, kwargs):
            if tool_name == "get_project_update_status":
                return {"success": True, "update_id": 99, "status": "pending"}
            return await _default_invoke(tool_name, kwargs)

        with (
            patch("agent_service.nodes.lightspeed._SYNC_POLL_INTERVAL", 0),
            patch("agent_service.nodes.lightspeed._SYNC_POLL_TIMEOUT", 0.01),
        ):
            result, _, invoke_mock = await _run_node(
                als_return=_ALS_RESPONSE,
                invoke_fn=sync_always_pending,
            )

        rr = result["remediation_result"]
        assert rr.success is False
        assert "timed out" in rr.output_summary
        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "upsert_job_template" not in tool_names

    async def test_no_log_event_empty_extra_vars(self):
        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            log_event=None,
            use_defaults=False,
        )

        rr = result["remediation_result"]
        assert rr.success is True

        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars == {}


# -- Pre-launch target verification --


class TestPreLaunchVerification:
    async def test_verify_retargets_pod_name_to_affected_component(self):
        async def verify_ok(tool_name, kwargs):
            if tool_name == "get_pod_spec":
                return {
                    "success": True,
                    "name": "orders-api-79f84f74d9-dp55t",
                    "namespace": kwargs.get("namespace", ""),
                    "spec": {},
                    "error": None,
                }
            return await _default_invoke(tool_name, kwargs)

        _, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=verify_ok,
            llm_summary={
                "affected_component": "deployment/orders-api (edge-site-02)",
                "edge_site_id": "edge-site-02",
            },
            resource_specs="Edge site: edge-site-02\nPod: orders-api",
        )

        gps_call = [c for c in invoke_mock.call_args_list if c[0][0] == "get_pod_spec"][0]
        gps_kwargs = gps_call[0][1]
        assert gps_kwargs["name"] == "orders-api"
        assert gps_kwargs["edge_site_id"] == "edge-site-02"

        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["pod_name"] == "orders-api-79f84f74d9-dp55t"

    async def test_verify_failure_blocks_launch(self):
        async def verify_fails(tool_name, kwargs):
            if tool_name == "get_pod_spec":
                return {"success": False, "name": "", "spec": {}, "error": "no pod matching 'x'"}
            return await _default_invoke(tool_name, kwargs)

        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=verify_fails,
            llm_summary={"affected_component": "orders-api"},
            resource_specs="Edge site: edge-1\nPod: orders-api",
        )

        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "launch_job" not in tool_names
        assert result["remediation_result"].success is False

    async def test_no_retarget_skips_verify_call(self):
        async def fail_if_verified(tool_name, kwargs):
            if tool_name == "get_pod_spec":
                return {"success": False, "name": "", "spec": {}, "error": "transient error"}
            return await _default_invoke(tool_name, kwargs)

        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=fail_if_verified,
        )

        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "get_pod_spec" not in tool_names
        assert "launch_job" in tool_names
        assert result["remediation_result"].success is True

    async def test_site_only_retarget_triggers_verify(self):
        verified = []

        async def track_verify(tool_name, kwargs):
            if tool_name == "get_pod_spec":
                verified.append(kwargs)
                return {"success": True, "name": "nginx-abc123", "namespace": "ns", "spec": {}}
            return await _default_invoke(tool_name, kwargs)

        _, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=track_verify,
            llm_summary={"edge_site_id": "edge-site-02"},
            resource_specs="Edge site: edge-site-02\nPod: nginx",
        )

        assert len(verified) == 1
        assert verified[0]["edge_site_id"] == "edge-site-02"
        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        assert launch_call[0][1]["extra_vars"]["edge_site_id"] == "edge-site-02"


# -- Multicluster credential --


class TestClusterName:
    async def test_edge_site_id_always_present(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)
        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        assert "credential_name" not in launch_call[0][1]
        assert launch_call[0][1]["extra_vars"]["edge_site_id"] == "edge-1"


class TestDeploymentNameExtraVar:
    async def test_default_launch_derives_deployment_name_from_pod(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)
        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["pod_name"] == "nginx-abc123"
        assert extra_vars["deployment_name"] == "nginx-abc123"

    async def test_grounded_component_sets_deployment_name_as_is(self):
        _, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            llm_summary={"affected_component": "nginx-ingress-controller"},
            cluster_events=[{"reason": "Pulled", "message": "nginx-ingress-controller image"}],
        )
        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["deployment_name"] == "nginx-ingress-controller"

    async def test_ungrounded_site_not_overlaid(self):
        # The component is confirmed live by the default get_pod_spec mock, so
        # _resolve_target retargets to it; the ungrounded site stays as-is.
        _, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            llm_summary={
                "affected_component": "totally-made-up",
                "edge_site_id": "other-cluster",
            },
            cluster_events=[{"reason": "Pulled", "message": "pulled image"}],
        )
        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["deployment_name"] == "totally-made-up"
        assert extra_vars["edge_site_id"] == "edge-1"

    async def test_grounded_edge_site_id_overlay(self):
        _, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            llm_summary={"edge_site_id": "edge-west"},
            resource_specs="Edge site: edge-west\nPod: orders-api",
        )
        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["edge_site_id"] == "edge-west"

    async def test_edge_site_id_not_overlaid_from_bare_event_mention(self):
        _, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            llm_summary={"edge_site_id": "edge-west"},
            cluster_events=[{"reason": "Pulled", "message": "seen on edge-west"}],
        )
        launch_call = [c for c in invoke_mock.call_args_list if c[0][0] == "launch_job"][0]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["edge_site_id"] == "edge-1"


# -- ALS failure --


class TestLightspeedNodeFailure:
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.HTTPStatusError(
                "500",
                request=httpx.Request("POST", "http://x"),
                response=httpx.Response(500),
            ),
            httpx.ConnectError("refused"),
            RuntimeError("boom"),
        ],
    )
    async def test_exceptions_return_failure(self, exc):
        result, _, invoke_mock = await _run_node(als_side_effect=exc)
        rr = result["remediation_result"]
        assert rr.success is False
        assert rr.job_id == ""
        assert rr.generated_playbook_name is None
        assert result["decision"] == "lightspeed"
        invoke_mock.assert_not_called()


# -- Playbook storage --


class TestPlaybookStorage:
    async def test_store_called_after_aap_success(self):
        store_mock = AsyncMock()
        with patch("agent_service.nodes.lightspeed.store_generated_playbook", store_mock):
            await _run_node(als_return=_ALS_RESPONSE)
            await asyncio.sleep(0)
        store_mock.assert_called_once()
        args = store_mock.call_args.args
        assert args[0] == "remediate-oomkilled-nginx-abc123"
        assert "hosts: all" in args[1]
        assert args[2] == "OOMKilled"
        assert args[3] == "Container killed by OOM"

    async def test_store_called_when_skip_aap(self):
        store_mock = AsyncMock()
        with (
            patch("agent_service.nodes.lightspeed.store_generated_playbook", store_mock),
            patch("agent_service.nodes.lightspeed.LIGHTSPEED_SKIP_AAP", True),
        ):
            await _run_node(als_return=_ALS_RESPONSE)
            await asyncio.sleep(0)
        store_mock.assert_called_once()

    async def test_store_not_called_on_als_failure(self):
        store_mock = AsyncMock()
        with patch("agent_service.nodes.lightspeed.store_generated_playbook", store_mock):
            await _run_node(als_side_effect=RuntimeError("boom"))
            await asyncio.sleep(0)
        store_mock.assert_not_called()

    async def test_store_not_called_when_no_lightspeed_url(self):
        store_mock = AsyncMock()
        with (
            patch("agent_service.nodes.lightspeed.store_generated_playbook", store_mock),
            patch("agent_service.nodes.lightspeed.LIGHTSPEED_URL", ""),
        ):
            await lightspeed_node(_state())
            await asyncio.sleep(0)
        store_mock.assert_not_called()


# -- Evidence summarization --


class TestSummarizeEvidence:
    async def test_parses_json_response(self):
        mock_response = MagicMock()
        mock_response.content = (
            '{"affected_component": "orders-api-f8ynqtska4", '
            '"root_cause": "CPU throttling", '
            '"remediation_steps": "increase CPU limit"}'
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("agent_service.nodes.lightspeed.get_llm", return_value=mock_llm):
            result = await _summarize_evidence(make_rca(), "some evidence")

        assert result["affected_component"] == "orders-api-f8ynqtska4"
        assert result["root_cause"] == "CPU throttling"
        assert result["remediation_steps"] == "increase CPU limit"

    async def test_parses_fenced_json(self):
        mock_response = MagicMock()
        mock_response.content = (
            "```json\n"
            '{"affected_component": "svc", "root_cause": "CPU throttling", '
            '"remediation_steps": "scale", "edge_site_id": "edge-west"}\n'
            "```"
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("agent_service.nodes.lightspeed.get_llm", return_value=mock_llm):
            result = await _summarize_evidence(make_rca(), "some evidence")

        assert result["affected_component"] == "svc"
        assert result["root_cause"] == "CPU throttling"
        assert result["remediation_steps"] == "scale"
        assert result["edge_site_id"] == "edge-west"

    async def test_empty_response_returns_empty_dict(self):
        mock_response = MagicMock()
        mock_response.content = ""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("agent_service.nodes.lightspeed.get_llm", return_value=mock_llm):
            result = await _summarize_evidence(make_rca(), "some evidence")

        assert result == {}

    async def test_invalid_json_returns_empty_dict(self):
        mock_response = MagicMock()
        mock_response.content = "not json at all"
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("agent_service.nodes.lightspeed.get_llm", return_value=mock_llm):
            result = await _summarize_evidence(make_rca(), "some evidence")

        assert result == {}


class TestSummarizeEvidenceIntegration:
    async def test_llm_summary_enriches_als_prompt(self):
        llm_summary = {
            "affected_component": "orders-api-f8ynqtska4-pod",
            "root_cause": "CPU contention on downstream service",
            "remediation_steps": "increase CPU limits, scale replicas",
        }
        _, als_mock, _ = await _run_node(
            als_return=_ALS_RESPONSE,
            llm_summary=llm_summary,
            cluster_events=[{"reason": "Pulled", "message": "pulled image"}],
        )

        prompt = als_mock.call_args[0][0]
        assert "orders-api-f8ynqtska4-pod" in prompt
        assert "CPU contention" in prompt
        assert "increase CPU limits" in prompt
        attachments = als_mock.call_args[0][1]
        attachment_content = " ".join(a["content"] for a in attachments)
        assert "pulled image" in attachment_content

    async def test_summarize_failure_falls_back_with_evidence(self):
        _, als_mock, _ = await _run_node(
            als_return=_ALS_RESPONSE,
            llm_summarize_side_effect=RuntimeError("LLM down"),
            cluster_events=[{"reason": "Pulled", "message": "pulled image"}],
        )

        prompt = als_mock.call_args[0][0]
        assert "nginx-abc123" in prompt
        assert "Container killed by OOM" in prompt


# -- _fix_patch_tasks --


def _make_playbook(uri_dict):
    return [{"name": "test play", "hosts": "localhost", "tasks": [{"name": "patch it", "uri": uri_dict}]}]


class TestFixPatchTasks:
    def test_string_body_converted_to_dict(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "body": '{"spec": {"replicas": 3}}',
        })
        _fix_patch_tasks(pb)
        uri = pb[0]["tasks"][0]["uri"]
        assert isinstance(uri["body"], dict)
        assert uri["body"]["spec"]["replicas"] == 3
        assert uri["body_format"] == "json"

    def test_strategic_merge_patch_header_set(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "headers": {"Authorization": "Bearer tok"},
            "body": {"spec": {}},
            "body_format": "json",
        })
        _fix_patch_tasks(pb)
        headers = pb[0]["tasks"][0]["uri"]["headers"]
        assert headers["Content-Type"] == _STRATEGIC_MERGE_PATCH
        assert headers["Authorization"] == "Bearer tok"

    def test_invalid_content_type_param_stripped(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "content_type": "application/strategic-merge-patch+json",
            "body": {"spec": {}},
        })
        _fix_patch_tasks(pb)
        uri = pb[0]["tasks"][0]["uri"]
        assert "content_type" not in uri
        assert uri["headers"]["Content-Type"] == _STRATEGIC_MERGE_PATCH

    def test_status_code_added_when_missing(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "body": {"spec": {}},
            "body_format": "json",
        })
        _fix_patch_tasks(pb)
        assert pb[0]["tasks"][0]["uri"]["status_code"] == 200

    def test_existing_status_code_preserved(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "body": {"spec": {}},
            "body_format": "json",
            "status_code": 201,
        })
        _fix_patch_tasks(pb)
        assert pb[0]["tasks"][0]["uri"]["status_code"] == 201

    def test_get_tasks_untouched(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "GET",
        })
        _fix_patch_tasks(pb)
        uri = pb[0]["tasks"][0]["uri"]
        assert "body_format" not in uri
        assert "headers" not in uri
        assert "status_code" not in uri

    def test_none_input_returns_none(self):
        assert _fix_patch_tasks(None) is None

    def test_non_list_input_returned_as_is(self):
        d = {"key": "val"}
        assert _fix_patch_tasks(d) is d

    def test_multiline_string_body(self):
        body_str = "spec:\n  template:\n    spec:\n      containers:\n        - name: app\n          resources:\n            limits:\n              memory: 512Mi\n"
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "body": body_str,
        })
        _fix_patch_tasks(pb)
        uri = pb[0]["tasks"][0]["uri"]
        assert isinstance(uri["body"], dict)
        assert uri["body"]["spec"]["template"]["spec"]["containers"][0]["name"] == "app"

    def test_headers_created_when_absent(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "body": {"spec": {}},
        })
        _fix_patch_tasks(pb)
        assert pb[0]["tasks"][0]["uri"]["headers"]["Content-Type"] == _STRATEGIC_MERGE_PATCH

    def test_wrong_content_type_overwritten(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "headers": {"Content-Type": "application/merge-patch+json"},
            "body": {"spec": {}},
        })
        _fix_patch_tasks(pb)
        assert pb[0]["tasks"][0]["uri"]["headers"]["Content-Type"] == _STRATEGIC_MERGE_PATCH

    def test_deployment_body_containers_wrapped_under_template(self):
        pb = _make_playbook({
            "url": "{{ hub_url }}/{{ edge_site_id }}/apis/apps/v1/namespaces/ns/deployments/myapp",
            "method": "PATCH",
            "body": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "256Mi"}}}]}},
        })
        _fix_patch_tasks(pb)
        body = pb[0]["tasks"][0]["uri"]["body"]
        assert "containers" not in body["spec"]
        assert body["spec"]["template"]["spec"]["containers"][0]["name"] == "app"

    def test_deployment_body_already_correct_not_double_wrapped(self):
        pb = _make_playbook({
            "url": "{{ hub_url }}/{{ edge_site_id }}/apis/apps/v1/namespaces/ns/deployments/myapp",
            "method": "PATCH",
            "body": {"spec": {"template": {"spec": {"containers": [{"name": "app"}]}}}},
        })
        _fix_patch_tasks(pb)
        body = pb[0]["tasks"][0]["uri"]["body"]
        assert body["spec"]["template"]["spec"]["containers"][0]["name"] == "app"
        assert "containers" not in body["spec"]

    def test_non_deployment_url_containers_not_wrapped(self):
        pb = _make_playbook({
            "url": "{{ hub_url }}/{{ edge_site_id }}/api/v1/namespaces/ns/pods/mypod",
            "method": "PATCH",
            "body": {"spec": {"containers": [{"name": "app"}]}},
        })
        _fix_patch_tasks(pb)
        body = pb[0]["tasks"][0]["uri"]["body"]
        assert body["spec"]["containers"][0]["name"] == "app"

    def test_json_patch_list_body_sets_json_patch_content_type(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "body": [{"op": "replace", "path": "/spec/replicas", "value": 3}],
        })
        _fix_patch_tasks(pb)
        uri = pb[0]["tasks"][0]["uri"]
        assert uri["headers"]["Content-Type"] == _JSON_PATCH
        assert uri["body_format"] == "json"
        assert isinstance(uri["body"], list)

    def test_bare_dict_play_is_sanitized(self):
        play = {
            "name": "single play",
            "hosts": "localhost",
            "tasks": [{"name": "patch it", "uri": {
                "url": "https://api/v1/...",
                "method": "PATCH",
                "body": '{"spec": {"replicas": 3}}',
            }}],
        }
        result = sanitize_playbook(play)
        assert isinstance(result, list)
        uri = result[0]["tasks"][0]["uri"]
        assert isinstance(uri["body"], dict)
        assert uri["headers"]["Content-Type"] == _STRATEGIC_MERGE_PATCH

    def test_apply_patch_content_type_preserved(self):
        pb = _make_playbook({
            "url": "https://api/v1/...",
            "method": "PATCH",
            "headers": {"Content-Type": _APPLY_PATCH},
            "body": {"spec": {}},
        })
        _fix_patch_tasks(pb)
        assert pb[0]["tasks"][0]["uri"]["headers"]["Content-Type"] == _APPLY_PATCH

    def test_field_manager_url_gets_apply_patch_content_type(self):
        pb = _make_playbook({
            "url": "https://api/v1/...?fieldManager=agent-service&force=true",
            "method": "PATCH",
            "body": {"spec": {}},
        })
        _fix_patch_tasks(pb)
        assert pb[0]["tasks"][0]["uri"]["headers"]["Content-Type"] == _APPLY_PATCH


# -- _fix_cluster_proxy_auth --


def _make_play_with_vars(play_vars, uri_dict):
    return [{"name": "test play", "hosts": "localhost", "vars": play_vars, "tasks": [{"name": "call api", "uri": uri_dict}]}]


class TestFixClusterProxyAuth:
    def test_k8s_auth_host_var_rewritten(self):
        pb = _make_play_with_vars(
            {"k8s_api_url": "{{ lookup('env', 'K8S_AUTH_HOST') }}"},
            {"url": "{{ k8s_api_url }}/apis/apps/v1/...", "method": "GET"},
        )
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["vars"]["k8s_api_url"] == "{{ hub_url }}/{{ edge_site_id }}"

    def test_k8s_auth_api_key_var_rewritten(self):
        pb = _make_play_with_vars(
            {"k8s_api_token": "{{ lookup('env', 'K8S_AUTH_API_KEY') }}"},
            {"url": "https://api/...", "method": "GET", "headers": {"Authorization": "Bearer {{ k8s_api_token }}"}},
        )
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["vars"]["k8s_api_token"] == "{{ token_acm }}"

    def test_inline_host_lookup_in_url_rewritten(self):
        pb = _make_play_with_vars(
            {},
            {"url": "{{ lookup('env', 'K8S_AUTH_HOST') }}/apis/apps/v1/...", "method": "GET"},
        )
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["tasks"][0]["uri"]["url"] == "{{ hub_url }}/{{ edge_site_id }}/apis/apps/v1/..."

    def test_inline_key_lookup_in_auth_header_rewritten(self):
        pb = _make_play_with_vars(
            {},
            {
                "url": "{{ lookup('env', 'K8S_AUTH_HOST') }}/apis/apps/v1/...",
                "method": "GET",
                "headers": {"Authorization": "Bearer {{ lookup('env', 'K8S_AUTH_API_KEY') }}"},
            },
        )
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["tasks"][0]["uri"]["headers"]["Authorization"] == "Bearer {{ token_acm }}"

    def test_unrelated_vars_untouched(self):
        pb = _make_play_with_vars(
            {"namespace": "prod", "deployment": "nginx"},
            {"url": "{{ hub_url }}/...", "method": "GET"},
        )
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["vars"]["namespace"] == "prod"
        assert pb[0]["vars"]["deployment"] == "nginx"

    def test_none_input_returns_none(self):
        assert _fix_cluster_proxy_auth(None) is None

    def test_non_list_input_returned_as_is(self):
        d = {"key": "val"}
        assert _fix_cluster_proxy_auth(d) is d

    def test_both_vars_rewritten_together(self):
        pb = _make_play_with_vars(
            {
                "k8s_api_url": "{{ lookup('env', 'K8S_AUTH_HOST') }}",
                "k8s_api_token": "{{ lookup('env', 'K8S_AUTH_API_KEY') }}",
                "namespace": "edge-site-01",
            },
            {"url": "{{ k8s_api_url }}/apis/apps/v1/...", "method": "PATCH", "headers": {"Authorization": "Bearer {{ k8s_api_token }}"}},
        )
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["vars"]["k8s_api_url"] == "{{ hub_url }}/{{ edge_site_id }}"
        assert pb[0]["vars"]["k8s_api_token"] == "{{ token_acm }}"
        assert pb[0]["vars"]["namespace"] == "edge-site-01"

    def test_no_vars_section_does_not_crash(self):
        pb = [{"name": "test", "hosts": "localhost", "tasks": [{"name": "t", "uri": {"url": "http://x", "method": "GET"}}]}]
        _fix_cluster_proxy_auth(pb)
        assert "vars" not in pb[0]

    def test_task_level_env_block_stripped(self):
        pb = [{"name": "p", "hosts": "localhost", "tasks": [
            {"name": "t", "ansible.builtin.uri": {"url": "https://x", "method": "PATCH"}, "env": {"K8S_AUTH_HOST": "x"}},
        ]}]
        _fix_cluster_proxy_auth(pb)
        assert "env" not in pb[0]["tasks"][0]

    def test_task_level_environment_block_stripped(self):
        pb = [{"name": "p", "hosts": "localhost", "tasks": [
            {"name": "t", "uri": {"url": "https://x", "method": "GET"}, "environment": {"K8S_AUTH_HOST": "x"}},
        ]}]
        _fix_cluster_proxy_auth(pb)
        assert "environment" not in pb[0]["tasks"][0]

    def test_url_username_and_password_stripped(self):
        pb = _make_play_with_vars(
            {},
            {
                "url": "{{ lookup('env', 'K8S_AUTH_HOST') }}/apis/apps/v1/...",
                "method": "PATCH",
                "url_username": "{{ lookup('env', 'K8S_AUTH_USERNAME') }}",
                "url_password": "{{ lookup('env', 'K8S_AUTH_API_KEY') }}",
                "force_basic_auth": True,
            },
        )
        _fix_cluster_proxy_auth(pb)
        uri = pb[0]["tasks"][0]["uri"]
        assert "url_username" not in uri
        assert "url_password" not in uri
        assert "force_basic_auth" not in uri
        assert uri["headers"]["Authorization"] == "Bearer {{ token_acm }}"

    def test_auth_header_added_when_missing_for_cluster_proxy_url(self):
        pb = _make_play_with_vars({}, {"url": "{{ hub_url }}/{{ edge_site_id }}/apis/v1/pods", "method": "GET"})
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["tasks"][0]["uri"]["headers"]["Authorization"] == "Bearer {{ token_acm }}"

    def test_auth_header_not_added_for_external_url(self):
        pb = _make_play_with_vars({}, {"url": "https://registry.example.com/config", "method": "GET"})
        _fix_cluster_proxy_auth(pb)
        assert "Authorization" not in pb[0]["tasks"][0]["uri"].get("headers", {})

    def test_auth_header_not_added_for_non_hub_url(self):
        for url in ["http://x", "https://localhost/api/v1/pods", "http://internal:8080/healthz"]:
            pb = _make_play_with_vars({}, {"url": url, "method": "GET"})
            _fix_cluster_proxy_auth(pb)
            assert "Authorization" not in pb[0]["tasks"][0]["uri"].get("headers", {}), f"token leaked to {url}"

    def test_inline_key_not_rewritten_for_non_hub_url(self):
        pb = _make_play_with_vars(
            {},
            {
                "url": "https://external-api.example.com/v1/status",
                "method": "GET",
                "headers": {"Authorization": "Bearer {{ lookup('env', 'K8S_AUTH_API_KEY') }}"},
            },
        )
        _fix_cluster_proxy_auth(pb)
        assert "K8S_AUTH_API_KEY" in pb[0]["tasks"][0]["uri"]["headers"]["Authorization"]


class TestQuoteJinja:
    def test_bare_value_quoted(self):
        result = quote_jinja("  name: {{ foo }}\n")
        assert '"{{ foo }}"' in result

    def test_list_item_quoted(self):
        result = quote_jinja("  - name: {{ bar }}\n")
        assert '"{{ bar }}"' in result

    def test_already_quoted_unchanged(self):
        line = '  name: "{{ foo }}"\n'
        assert quote_jinja(line) == line

    def test_inline_value_quoted(self):
        line = "  Authorization: Bearer {{ token_acm }}\n"
        assert quote_jinja(line) == '  Authorization: "Bearer {{ token_acm }}"\n'

    def test_multiline(self):
        text = "  url: {{ hub_url }}\n  name: {{ x }}\n  ok: already\n"
        result = quote_jinja(text)
        assert '  url: "{{ hub_url }}"' in result
        assert '  name: "{{ x }}"' in result
        assert "  ok: already" in result


class TestFixAnsibleFacts:
    def test_replaces_ansible_date_time_iso8601(self):
        text = "    restartedAt: {{ ansible_date_time.iso8601 }}"
        assert "ansible_date_time" not in fix_ansible_facts(text)
        assert "now(utc=True" in fix_ansible_facts(text)

    def test_leaves_other_expressions_unchanged(self):
        text = "    url: {{ hub_url }}/{{ edge_site_id }}/apis"
        assert fix_ansible_facts(text) == text

    def test_replaces_with_extra_whitespace(self):
        text = "    restartedAt: {{  ansible_date_time.iso8601  }}"
        assert "ansible_date_time" not in fix_ansible_facts(text)


class TestQuoteJinjaRegex:
    def test_mixed_jinja_and_literal(self):
        """URL with Jinja and trailing literal text must be quoted."""
        from agent_service.playbook_sanitize import quote_jinja
        text = "    url: {{ hub_url }}/{{ edge_site_id }}/apis/v1/namespaces"
        result = quote_jinja(text)
        assert result == '    url: "{{ hub_url }}/{{ edge_site_id }}/apis/v1/namespaces"'

    def test_hyphenated_key(self):
        from agent_service.playbook_sanitize import quote_jinja
        text = "    Content-Type: {{ content_type }}"
        result = quote_jinja(text)
        assert result == '    Content-Type: "{{ content_type }}"'

    def test_already_quoted_not_double_quoted(self):
        from agent_service.playbook_sanitize import quote_jinja
        text = '    url: "{{ already_quoted }}"'
        result = quote_jinja(text)
        assert result == '    url: "{{ already_quoted }}"'


# -- _iter_uri_tasks --


def _play_with_block(uri_dicts_by_section):
    """Build a play with block/rescue/always structure containing uri tasks."""
    block_tasks = []
    for section, uri_dict in uri_dicts_by_section.items():
        if section == "block":
            block_tasks.append({"name": f"{section} task", "uri": uri_dict})
        # rescue/always are siblings of block inside the same task dict
    task_entry = {"block": [{"name": "block task", "uri": uri_dicts_by_section.get("block", {"url": "http://x", "method": "GET"})}]}
    if "rescue" in uri_dicts_by_section:
        task_entry["rescue"] = [{"name": "rescue task", "uri": uri_dicts_by_section["rescue"]}]
    if "always" in uri_dicts_by_section:
        task_entry["always"] = [{"name": "always task", "uri": uri_dicts_by_section["always"]}]
    return {"name": "test", "hosts": "localhost", "tasks": [task_entry]}


class TestIterUriTasks:
    def test_flat_tasks(self):
        play = {"name": "p", "hosts": "localhost", "tasks": [
            {"name": "t1", "uri": {"url": "http://a", "method": "GET"}},
            {"name": "t2", "ansible.builtin.uri": {"url": "http://b", "method": "POST"}},
        ]}
        results = list(_iter_uri_tasks(play))
        assert len(results) == 2
        assert results[0][1]["url"] == "http://a"
        assert results[1][1]["url"] == "http://b"

    def test_block_rescue_always(self):
        play = _play_with_block({
            "block": {"url": "http://block", "method": "GET"},
            "rescue": {"url": "http://rescue", "method": "GET"},
            "always": {"url": "http://always", "method": "GET"},
        })
        results = list(_iter_uri_tasks(play))
        urls = [uri["url"] for _, uri in results]
        assert "http://block" in urls
        assert "http://rescue" in urls
        assert "http://always" in urls

    def test_non_uri_tasks_skipped(self):
        play = {"name": "p", "hosts": "localhost", "tasks": [
            {"name": "debug", "debug": {"msg": "hello"}},
            {"name": "api call", "uri": {"url": "http://x", "method": "GET"}},
        ]}
        results = list(_iter_uri_tasks(play))
        assert len(results) == 1

    def test_empty_tasks(self):
        play = {"name": "p", "hosts": "localhost"}
        assert list(_iter_uri_tasks(play)) == []

    def test_pre_tasks_and_post_tasks(self):
        play = {"name": "p", "hosts": "localhost",
                "pre_tasks": [{"name": "pre", "uri": {"url": "http://pre", "method": "GET"}}],
                "post_tasks": [{"name": "post", "uri": {"url": "http://post", "method": "GET"}}],
                "tasks": []}
        results = list(_iter_uri_tasks(play))
        urls = [uri["url"] for _, uri in results]
        assert "http://pre" in urls
        assert "http://post" in urls


# -- _strip_dangerous_headers --


class TestStripDangerousHeaders:
    def test_impersonate_user_stripped(self):
        pb = _make_playbook({
            "url": "http://x", "method": "GET",
            "headers": {"Authorization": "Bearer tok", "Impersonate-User": "admin"},
        })
        _strip_dangerous_headers(pb)
        headers = pb[0]["tasks"][0]["uri"]["headers"]
        assert "Impersonate-User" not in headers
        assert headers["Authorization"] == "Bearer tok"

    def test_impersonate_group_stripped(self):
        pb = _make_playbook({
            "url": "http://x", "method": "GET",
            "headers": {"Impersonate-Group": "system:masters"},
        })
        _strip_dangerous_headers(pb)
        assert "Impersonate-Group" not in pb[0]["tasks"][0]["uri"]["headers"]

    def test_impersonate_extra_stripped(self):
        pb = _make_playbook({
            "url": "http://x", "method": "GET",
            "headers": {"Impersonate-Extra-scopes": "admin"},
        })
        _strip_dangerous_headers(pb)
        assert "Impersonate-Extra-scopes" not in pb[0]["tasks"][0]["uri"]["headers"]

    def test_x_forwarded_for_stripped(self):
        pb = _make_playbook({
            "url": "http://x", "method": "GET",
            "headers": {"X-Forwarded-For": "10.0.0.1", "Authorization": "Bearer tok"},
        })
        _strip_dangerous_headers(pb)
        headers = pb[0]["tasks"][0]["uri"]["headers"]
        assert "X-Forwarded-For" not in headers
        assert headers["Authorization"] == "Bearer tok"

    def test_x_real_ip_stripped(self):
        pb = _make_playbook({
            "url": "http://x", "method": "GET",
            "headers": {"X-Real-IP": "10.0.0.1"},
        })
        _strip_dangerous_headers(pb)
        assert "X-Real-IP" not in pb[0]["tasks"][0]["uri"]["headers"]

    def test_normal_headers_preserved(self):
        pb = _make_playbook({
            "url": "http://x", "method": "GET",
            "headers": {"Authorization": "Bearer tok", "Content-Type": "application/json", "Accept": "application/json"},
        })
        _strip_dangerous_headers(pb)
        headers = pb[0]["tasks"][0]["uri"]["headers"]
        assert headers["Authorization"] == "Bearer tok"
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    def test_no_headers_no_crash(self):
        pb = _make_playbook({"url": "http://x", "method": "GET"})
        _strip_dangerous_headers(pb)

    def test_none_input_returns_none(self):
        assert _strip_dangerous_headers(None) is None

    def test_block_rescue_tasks_also_stripped(self):
        play = _play_with_block({
            "block": {"url": "http://x", "method": "GET", "headers": {"Impersonate-User": "admin"}},
            "rescue": {"url": "http://y", "method": "GET", "headers": {"X-Forwarded-For": "1.2.3.4"}},
        })
        pb = [play]
        _strip_dangerous_headers(pb)
        for task, uri in _iter_uri_tasks(play):
            headers = uri.get("headers", {})
            assert "Impersonate-User" not in headers
            assert "X-Forwarded-For" not in headers


class TestIsDangerousHeaderCaseInsensitive:
    @pytest.mark.parametrize(
        "header",
        [
            "impersonate-User",
            "IMPERSONATE-GROUP",
            "Impersonate-Extra-foo",
            "X-Forwarded-For",
            "x-forwarded-for",
            "X-FORWARDED-FOR",
            "x-Real-IP",
            "X-REAL-IP",
        ],
    )
    def test_mixed_case_detected(self, header):
        assert _is_dangerous_header(header) is True


# -- validate_certs injection --


class TestValidateCertsInjection:
    def test_validate_certs_injected_on_uri_task(self):
        pb = _make_play_with_vars({}, {"url": "https://x", "method": "GET"})
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["tasks"][0]["uri"]["validate_certs"] is False

    def test_explicit_validate_certs_preserved(self):
        pb = _make_play_with_vars({}, {"url": "https://x", "method": "GET", "validate_certs": True})
        _fix_cluster_proxy_auth(pb)
        assert pb[0]["tasks"][0]["uri"]["validate_certs"] is True

    def test_validate_certs_in_block_task(self):
        play = _play_with_block({
            "block": {"url": "http://x", "method": "GET"},
        })
        _fix_cluster_proxy_auth([play])
        for task, uri in _iter_uri_tasks(play):
            assert uri.get("validate_certs") is False


# -- block/rescue/always in post-processing --


class TestBlockRescuePostProcessing:
    def test_auth_header_injected_in_block_task(self):
        play = _play_with_block({
            "block": {"url": "{{ hub_url }}/{{ edge_site_id }}/api/v1/pods", "method": "GET"},
        })
        _fix_cluster_proxy_auth([play])
        for _, uri in _iter_uri_tasks(play):
            assert uri["headers"]["Authorization"] == "Bearer {{ token_acm }}"

    def test_patch_fixed_in_rescue_task(self):
        play = _play_with_block({
            "block": {"url": "http://x", "method": "GET"},
            "rescue": {"url": "http://y", "method": "PATCH", "body": {"spec": {}},
                       "headers": {"Content-Type": "application/merge-patch+json"}},
        })
        _fix_patch_tasks([play])
        for _, uri in _iter_uri_tasks(play):
            if str(uri.get("method", "")).upper() == "PATCH":
                assert uri["headers"]["Content-Type"] == _STRATEGIC_MERGE_PATCH

    def test_env_stripped_from_block_task(self):
        play = {"name": "p", "hosts": "localhost", "tasks": [
            {"block": [
                {"name": "t", "uri": {"url": "http://x", "method": "GET"}, "env": {"K8S_AUTH_HOST": "x"}},
            ]},
        ]}
        _fix_cluster_proxy_auth([play])
        block_task = play["tasks"][0]["block"][0]
        assert "env" not in block_task


# -- prompt regression guard --


class TestParseSummaryJsonGreedy:
    def test_preamble_with_curly_braces(self):
        from agent_service.nodes.lightspeed import _parse_summary_json

        text = 'Here is the analysis {note: this is context}. The JSON:\n{"affected_component": "nginx", "root_cause": "OOM"}'
        result = _parse_summary_json(text)
        assert result["affected_component"] == "nginx"
        assert result["root_cause"] == "OOM"

    def test_json_with_trailing_text(self):
        from agent_service.nodes.lightspeed import _parse_summary_json

        text = '{"affected_component": "redis", "root_cause": "connection refused"}\nHope this helps!'
        result = _parse_summary_json(text)
        assert result["affected_component"] == "redis"


class TestPromptRegressionGuard:
    def test_prompt_does_not_contain_never_use_lookup(self):
        from agent_service.config import LIGHTSPEED_PROMPT_TEMPLATE
        assert "NEVER use lookup" not in LIGHTSPEED_PROMPT_TEMPLATE

    def test_prompt_contains_available_variables(self):
        from agent_service.config import LIGHTSPEED_PROMPT_TEMPLATE
        assert "hub_url" in LIGHTSPEED_PROMPT_TEMPLATE
        assert "token_acm" in LIGHTSPEED_PROMPT_TEMPLATE
        assert "edge_site_id" in LIGHTSPEED_PROMPT_TEMPLATE
