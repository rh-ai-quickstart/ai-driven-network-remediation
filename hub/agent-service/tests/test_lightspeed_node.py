import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import yaml
from helpers import (
    RESTART_AT,
    RESTART_EVIDENCE,
    RESTART_VERIFICATION,
    make_log_event,
    make_rca,
    restart_playbook,
    selected_state,
    update_selection,
)

from agent_service.config import PLAYBOOK_VALIDATION_TIMEOUT_SECONDS
from agent_service.graph import _route_after_lightspeed
from agent_service.models import (
    IncidentState,
)
from agent_service.nodes._aap_job import JobOutcome
from agent_service.nodes.lightspeed import (
    _build_attachments,
    _build_playbook_name,
    _extract_yaml,
    lightspeed_node,
)
from agent_service.playbook_sanitize import quote_jinja

_ALS_RESPONSE = {
    "conversation_id": "conv-123",
    "response": yaml.safe_dump(restart_playbook()),
    "referenced_documents": [],
    "truncated": False,
    "input_tokens": 100,
    "output_tokens": 50,
}

_UPSERT_OK = {
    "success": True,
    "template_id": 7,
    "created": True,
    "template_name": "restart-selected-deployment",
}

_LAUNCH_OK = {
    "success": True,
    "job_id": 42,
    "status": "pending",
    "template_name": "restart-selected-deployment",
}


def _state(rca=None, log_event=None, use_defaults=True, **extra):
    state = selected_state(**extra)
    if rca is not None or not use_defaults:
        state.root_cause_analysis = rca
    if log_event is not None or not use_defaults:
        state.log_event = log_event
    return state


@pytest.fixture(autouse=True)
def isolate_storage_and_mode(monkeypatch):
    monkeypatch.setattr("agent_service.nodes.lightspeed.store_generated_playbook", AsyncMock())
    monkeypatch.setattr("agent_service.nodes.lightspeed.now_iso", lambda: RESTART_AT)
    monkeypatch.setattr("agent_service.nodes.lightspeed.LIGHTSPEED_SKIP_AAP", False)


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

_JOB_STATUS_OK = {
    "success": True,
    "job_id": 42,
    "status": "successful",
    "elapsed": 3.5,
    "finished": "2026-07-28T12:01:00Z",
    "failed": False,
    "result_traceback": "",
}

_JOB_OUTPUT_OK = {"success": True, "output": "PLAY RECAP ok=1 changed=1 failed=0", "job_id": 42}


async def _default_invoke(tool_name, kwargs):
    if tool_name == "validate_playbook":
        return {"success": True, "valid": True}
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
    if tool_name == "get_job_status":
        return _JOB_STATUS_OK
    if tool_name == "get_job_output":
        return _JOB_OUTPUT_OK
    if tool_name == "launch_job":
        return _LAUNCH_OK
    raise ValueError(f"Unexpected tool: {tool_name}")


async def _run_node(
    als_return=None,
    als_side_effect=None,
    invoke_fn=None,
    **state_kw,
):
    als_mock = AsyncMock(
        return_value=als_return,
        side_effect=als_side_effect,
    )
    if invoke_fn is None:
        invoke_fn = _default_invoke

    async def invoke_with_options(tool_name, kwargs, **request_options):
        return await invoke_fn(tool_name, kwargs)

    invoke_mock = AsyncMock(side_effect=invoke_with_options)
    with (
        patch("agent_service.nodes.lightspeed.LIGHTSPEED_URL", "https://als-stub"),
        patch("agent_service.nodes.lightspeed._call_als", als_mock),
        patch("agent_service.nodes.lightspeed._invoke_tool", invoke_mock),
        patch("agent_service.nodes._aap_job._invoke_tool", invoke_mock),
    ):
        result = await lightspeed_node(_state(**state_kw))
        await asyncio.sleep(0)
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


# -- AAP execution (commit + sync + upsert + launch) --


class TestAAPExecution:

    async def test_upsert_uses_committed_file_path(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        upsert_call = [c for c in invoke_mock.call_args_list if c[0][0] == "upsert_job_template"][0]
        args = upsert_call[0][1]
        assert args["playbook"] == "playbooks/restart-selected-deployment.yaml"
        assert args["base_template_name"] == "lightspeed-runner"
        assert args["template_name"] == "restart-selected-deployment"

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
        assert "commit_playbook" in rr.output_summary
        assert "gitea unreachable" in rr.output_summary
        tool_names = [c[0][0] for c in invoke_mock.call_args_list]
        assert "sync_project" not in tool_names
        assert "upsert_job_template" not in tool_names
        assert "launch_job" not in tool_names

    async def test_commit_failure_without_error_reports_failed_step(self):
        async def commit_fails(tool_name, kwargs):
            if tool_name == "commit_playbook":
                return {"success": False}
            return await _default_invoke(tool_name, kwargs)

        result, _, _ = await _run_node(als_return=_ALS_RESPONSE, invoke_fn=commit_fails)

        assert result["remediation_result"].output_summary == "commit_playbook failed"

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
        assert str(exc) in rr.output_summary
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
        assert args[0] == "restart-selected-deployment"
        assert "hosts: localhost" in args[1]
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

    async def test_store_called_after_job_success(self):
        store_mock = AsyncMock()
        with patch("agent_service.nodes.lightspeed.store_generated_playbook", store_mock):
            result, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)
            await asyncio.sleep(0)

        rr = result["remediation_result"]
        assert rr.success is True
        assert rr.timed_out is False
        assert rr.tool_used == "aap"
        assert rr.action_taken == rr.generated_playbook_name
        assert "get_job_status" in [c[0][0] for c in invoke_mock.call_args_list]
        store_mock.assert_called_once()

    async def test_store_not_called_on_job_failure(self):
        async def job_fails(tool_name, kwargs):
            if tool_name == "get_job_status":
                return {
                    "success": True,
                    "job_id": 42,
                    "status": "failed",
                    "elapsed": 1.0,
                    "finished": "2026-07-28T12:01:00Z",
                    "failed": True,
                    "result_traceback": "fatal: task failed",
                }
            return await _default_invoke(tool_name, kwargs)

        store_mock = AsyncMock()
        with patch("agent_service.nodes.lightspeed.store_generated_playbook", store_mock):
            result, _, _ = await _run_node(als_return=_ALS_RESPONSE, invoke_fn=job_fails)
            await asyncio.sleep(0)

        rr = result["remediation_result"]
        assert rr.success is False
        assert rr.tool_used == "aap"
        assert rr.action_taken == rr.generated_playbook_name
        assert rr.job_id == "42"
        assert "fatal: task failed" in rr.output_summary
        store_mock.assert_not_called()
        state = IncidentState(raw_event="x", remediation_result=rr)
        assert _route_after_lightspeed(state) == "escalate"

    async def test_store_not_called_on_job_timeout(self):
        timed_out = JobOutcome(
            success=False,
            timed_out=True,
            elapsed=120.0,
            timestamp="2026-07-28T12:02:00Z",
            output_summary="Job 42 timed out",
            output_text="",
        )
        store_mock = AsyncMock()
        with (
            patch("agent_service.nodes.lightspeed.store_generated_playbook", store_mock),
            patch("agent_service.nodes.lightspeed.evaluate_job", AsyncMock(return_value=timed_out)),
        ):
            result, _, _ = await _run_node(als_return=_ALS_RESPONSE)
            await asyncio.sleep(0)

        rr = result["remediation_result"]
        assert rr.success is False
        assert rr.timed_out is True
        store_mock.assert_not_called()
        state = IncidentState(raw_event="x", remediation_result=rr)
        assert _route_after_lightspeed(state) == "escalate"


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


class TestQuoteJinjaRegex:
    def test_mixed_jinja_and_literal(self):
        """URL with Jinja and trailing literal text must be quoted."""

        text = "    url: {{ hub_url }}/{{ edge_site_id }}/apis/v1/namespaces"
        result = quote_jinja(text)
        assert result == '    url: "{{ hub_url }}/{{ edge_site_id }}/apis/v1/namespaces"'

    def test_hyphenated_key(self):

        text = "    Content-Type: {{ content_type }}"
        result = quote_jinja(text)
        assert result == '    Content-Type: "{{ content_type }}"'

    def test_already_quoted_not_double_quoted(self):

        text = '    url: "{{ already_quoted }}"'
        result = quote_jinja(text)
        assert result == '    url: "{{ already_quoted }}"'


# -- prompt regression guard --


class TestPromptRegressionGuard:
    def test_prompt_does_not_contain_never_use_lookup(self):
        from agent_service.config import LIGHTSPEED_PROMPT_TEMPLATE

        assert "NEVER use lookup" not in LIGHTSPEED_PROMPT_TEMPLATE

    def test_prompt_contains_available_variables(self):
        from agent_service.config import DEPLOYMENT_MODE
        from agent_service.nodes.lightspeed import _build_prompt

        rca = selected_state().root_cause_analysis
        prompt = _build_prompt(rca, rca.selected_action)
        expected_variables = (
            ("K8S_AUTH_HOST", "K8S_AUTH_API_KEY", "deployment_name")
            if DEPLOYMENT_MODE == "single-cluster"
            else ("hub_url", "token_acm", "edge_site_id")
        )
        for variable in expected_variables:
            assert variable in prompt


@pytest.mark.asyncio
async def test_generated_playbook_uses_scoped_aap_resources(monkeypatch):
    prefix = "hub-team-a-"
    monkeypatch.setattr("agent_service.nodes.lightspeed.AAP_RESOURCE_PREFIX", prefix)
    monkeypatch.setattr("agent_service.nodes.lightspeed.AAP_LIGHTSPEED_TEMPLATE", prefix + "lightspeed-runner")
    monkeypatch.setattr("agent_service.nodes.lightspeed.GITEA_PROJECT_NAME", prefix + "lightspeed-generated")
    outcome, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)
    calls = {call.args[0]: call.args[1] for call in invoke_mock.call_args_list}
    template_name = prefix + "restart-selected-deployment"
    assert calls["commit_playbook"]["playbook_name"] == template_name
    assert calls["sync_project"]["project_name"] == prefix + "lightspeed-generated"
    assert calls["upsert_job_template"]["project_name"] == prefix + "lightspeed-generated"
    assert calls["upsert_job_template"]["base_template_name"] == prefix + "lightspeed-runner"
    assert calls["upsert_job_template"]["template_name"] == template_name
    assert calls["launch_job"]["job_template_name"] == template_name
    assert outcome["remediation_result"].generated_template_name == template_name


async def test_prompt_and_launch_preserve_the_noc_selection():
    outcome, generation, invoke = await _run_node(als_return=_ALS_RESPONSE)
    assert outcome["remediation_result"].success is True
    prompt, _ = generation.call_args.args
    for selected_value in ("orders-api", "prod", "edge-1", RESTART_AT, RESTART_EVIDENCE):
        assert selected_value in prompt
    calls = {call.args[0]: call.args[1] for call in invoke.call_args_list}
    assert calls["launch_job"]["extra_vars"] == {
        "deployment": "orders-api",
        "deployment_name": "orders-api",
        "namespace": "prod",
        "edge_site_id": "edge-1",
        "resource_kind": "Deployment",
        "resource_name": "orders-api",
        "container_name": "",
        "remediation_parameters": {"restart_at": RESTART_AT},
    }
    assert yaml.safe_load(calls["commit_playbook"]["playbook_content"]) == restart_playbook()
    assert "get_deployment" not in calls


async def test_missing_selection_stops_before_generation_or_publication():
    outcome, generation, invoke = await _run_node(als_return=_ALS_RESPONSE, rca=make_rca())
    assert outcome["remediation_result"].success is False
    assert _route_after_lightspeed(IncidentState(raw_event="x", **outcome)) == "escalate"
    generation.assert_not_called()
    invoke.assert_not_called()


_CPU_GOAL = (
    "Increase the CPU limit of container microservice-b in Deployment microservice-b to 250m, "
    "preserve its other resources, and verify the rollout"
)
_CPU_EVIDENCE = "microservice-b has a 25m CPU limit and requests take one second"
_CPU_VERIFICATION = "The selected container has a 250m CPU limit and the Deployment rollout completes"
_CPU_PLAYBOOK = """- name: Increase downstream CPU limit
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Update only the affected container CPU limit
      kubernetes.core.k8s:
        state: patched
        wait: true
        definition:
          apiVersion: apps/v1
          kind: Deployment
          metadata:
            name: '{{ resource_name }}'
            namespace: '{{ namespace }}'
          spec:
            template:
              spec:
                containers:
                  - name: '{{ container_name }}'
                    resources:
                      limits:
                        cpu: '{{ remediation_parameters.cpu_limit }}'
"""


async def test_cpu_limit_change_reaches_als_and_launches_for_downstream_target():
    state = selected_state()
    state.root_cause_analysis.failure_type = "CPUThrottling"
    update_selection(
        state,
        goal=_CPU_GOAL,
        target={"name": "microservice-b", "container": "microservice-b", "namespace": "edge-ofridman"},
        parameters={"cpu_limit": "250m"},
        evidence=[_CPU_EVIDENCE],
        verification=[_CPU_VERIFICATION],
    )
    outcome, generation, invoke = await _run_node(als_return={"response": _CPU_PLAYBOOK}, rca=state.root_cause_analysis)
    assert outcome["remediation_result"].success is True
    prompt, _ = generation.call_args.args
    assert all(value in prompt for value in (_CPU_GOAL, _CPU_EVIDENCE, "250m"))
    calls = {call.args[0]: call.args[1] for call in invoke.call_args_list}
    launch_vars = calls["launch_job"]["extra_vars"]
    assert launch_vars["deployment_name"] == launch_vars["resource_name"] == "microservice-b"
    assert launch_vars["container_name"] == "microservice-b"
    assert launch_vars["namespace"] == "edge-ofridman"
    assert launch_vars["remediation_parameters"] == {"cpu_limit": "250m"}
    assert yaml.safe_load(calls["commit_playbook"]["playbook_content"]) == yaml.safe_load(_CPU_PLAYBOOK)


@pytest.mark.parametrize("include_uid", [False, True])
async def test_missing_namespace_is_resolved_identically_for_als_and_aap(include_uid):
    state = selected_state(site="edge-site-01", namespace="", deployment="microservice-b")
    update_selection(state, goal=_CPU_GOAL, parameters={"cpu_limit": "250m"})
    observed_target = {
        "edge_site_id": "edge-site-01",
        "namespace": "edge-ofridman",
        "deployment": "microservice-b",
    }
    if include_uid:
        observed_target["uid"] = "observed-deployment-uid"
    outcome, generation, invoke = await _run_node(
        als_return={"response": _CPU_PLAYBOOK},
        rca=state.root_cause_analysis,
        deployment_observations=[observed_target],
    )
    assert outcome["remediation_result"].success is True
    prompt = generation.call_args.args[0]
    prompt_target = json.loads(prompt.split("\nTARGET\n")[1].split("\n\nOBSERVED FACTS")[0])
    launch_vars = next(call.args[1]["extra_vars"] for call in invoke.call_args_list if call.args[0] == "launch_job")
    assert prompt_target["namespace"] == launch_vars["namespace"] == "edge-ofridman"
    assert prompt_target["name"] == launch_vars["resource_name"] == "microservice-b"
    assert state.root_cause_analysis.selected_action.target.namespace == ""


@pytest.mark.parametrize("observed_namespaces", [[], ["team-a", "team-b"]])
async def test_unresolved_namespace_stops_before_generation_or_aap(observed_namespaces):
    state = selected_state(namespace="")
    observations = [
        {"edge_site_id": "edge-1", "namespace": namespace, "deployment": "orders-api", "uid": namespace}
        for namespace in observed_namespaces
    ]
    outcome, generation, invoke = await _run_node(
        als_return=_ALS_RESPONSE,
        rca=state.root_cause_analysis,
        deployment_observations=observations,
        log_event=make_log_event(namespace=""),
    )
    assert outcome["remediation_result"].success is False
    assert "namespace" in outcome["remediation_result"].output_summary.lower()
    assert _route_after_lightspeed(IncidentState(raw_event="x", **outcome)) == "escalate"
    generation.assert_not_called()
    invoke.assert_not_called()


@pytest.mark.parametrize("identity_override", [{"edge_site_id": "other-site"}, {"deployment": "other-app"}])
async def test_namespace_is_not_taken_from_another_target(identity_override):
    state = selected_state(namespace="")
    observation = {
        "edge_site_id": "edge-1",
        "namespace": "observed-namespace",
        "deployment": "orders-api",
        **identity_override,
    }
    outcome, generation, invoke = await _run_node(
        als_return=_ALS_RESPONSE,
        rca=state.root_cause_analysis,
        deployment_observations=[observation],
        log_event=make_log_event(namespace=""),
    )
    assert outcome["remediation_result"].success is False
    assert "namespace is missing" in outcome["remediation_result"].output_summary
    generation.assert_not_called()
    invoke.assert_not_called()


async def test_missing_namespace_uses_confirmed_exact_deployment_for_generation_and_launch():
    state = selected_state(namespace="", deployment="microservice-b")
    confirmed = {"success": True, "deployment": "microservice-b", "namespace": "edge-ofridman"}

    async def lookup_then_invoke(tool_name, kwargs):
        if tool_name == "get_deployment":
            return confirmed
        return await _default_invoke(tool_name, kwargs)

    outcome, generation, invoke = await _run_node(
        als_return=_ALS_RESPONSE,
        invoke_fn=lookup_then_invoke,
        rca=state.root_cause_analysis,
        log_event=make_log_event(namespace="edge-ofridman"),
    )
    assert outcome["remediation_result"].success is True
    assert invoke.call_args_list[0].args == (
        "get_deployment",
        {"deployment": "microservice-b", "namespace": "edge-ofridman", "edge_site_id": "edge-1"},
    )
    prompt_target = json.loads(generation.call_args.args[0].split("\nTARGET\n")[1].split("\n\nOBSERVED FACTS")[0])
    launch = next(call.args[1]["extra_vars"] for call in invoke.call_args_list if call.args[0] == "launch_job")
    assert prompt_target["namespace"] == launch["namespace"] == "edge-ofridman"
    assert prompt_target["name"] == launch["resource_name"] == "microservice-b"
    assert state.root_cause_analysis.selected_action.target.namespace == ""


@pytest.mark.parametrize(
    "lookup_reply",
    [
        {"success": False, "error": "NotFound"},
        {"success": True, "deployment": "wrong-name", "namespace": "prod"},
        {"success": True, "deployment": "orders-api", "namespace": "wrong-namespace"},
        {"success": True, "deployment": "orders-api", "namespace": "prod", "edge_site_id": "wrong-site"},
    ],
)
async def test_unconfirmed_namespace_lookup_stops_before_als_or_aap(lookup_reply):
    selected = selected_state(namespace="").root_cause_analysis
    lookup = AsyncMock(return_value=lookup_reply)
    outcome, generation, invoke = await _run_node(als_return=_ALS_RESPONSE, invoke_fn=lookup, rca=selected)
    assert outcome["remediation_result"].success is False
    assert "namespace lookup" in outcome["remediation_result"].output_summary
    generation.assert_not_called()
    assert [call.args[0] for call in invoke.call_args_list] == ["get_deployment"]


async def test_namespace_lookup_transport_error_stops_before_als_or_aap():
    selected = selected_state(namespace="").root_cause_analysis
    lookup = AsyncMock(side_effect=httpx.ConnectError("Deployment API unavailable"))
    outcome, generation, invoke = await _run_node(als_return=_ALS_RESPONSE, invoke_fn=lookup, rca=selected)
    assert outcome["remediation_result"].success is False
    assert "Deployment API unavailable" in outcome["remediation_result"].output_summary
    generation.assert_not_called()
    assert [call.args[0] for call in invoke.call_args_list] == ["get_deployment"]


@pytest.mark.parametrize(
    "kind, namespace, goal, parameters",
    [
        (
            "ConfigMap",
            "dns-system",
            "Repair the upstream DNS configuration and verify name resolution",
            {"upstream": "10.0.0.53"},
        ),
        (
            "Node",
            "",
            "Repair the diagnosed node configuration and verify connectivity",
            {"settings": {"enabled": True}},
        ),
        (
            "CustomResource",
            "other-team",
            "Apply the observed vendor-specific repair and verify service health",
            {"count": 2},
        ),
    ],
)
async def test_action_families_and_resource_scopes_are_not_allowlisted(kind, namespace, goal, parameters):
    state = selected_state()
    update_selection(
        state,
        goal=goal,
        target={
            "kind": kind,
            "namespace": namespace,
            "cluster_scoped": not namespace,
            "name": "affected-resource",
            "edge_site_id": "edge-2",
        },
        parameters=parameters,
        verification=["The affected service is healthy"],
    )
    generated = [{"name": "Repair selected resource", "hosts": "localhost", "roles": ["vendor.repair"]}]
    outcome, generation, invoke = await _run_node(
        als_return={"response": yaml.safe_dump(generated)}, rca=state.root_cause_analysis
    )
    assert outcome["remediation_result"].success is True
    assert goal in generation.call_args.args[0]
    calls = {call.args[0]: call.args[1] for call in invoke.call_args_list}
    launch_vars = calls["launch_job"]["extra_vars"]
    assert launch_vars["resource_kind"] == kind
    assert launch_vars["resource_name"] == "affected-resource"
    assert launch_vars["namespace"] == namespace
    assert launch_vars["edge_site_id"] == "edge-2"
    assert launch_vars["remediation_parameters"] == parameters
    assert "deployment_name" not in launch_vars
    assert "deployment" not in launch_vars
    assert yaml.safe_load(calls["commit_playbook"]["playbook_content"]) == generated


@pytest.mark.parametrize(
    "generated",
    [
        [{"hosts": "localhost", "tasks": [{"block": [{"ansible.builtin.command": "repair-service"}]}]}],
        [{"hosts": "localhost", "pre_tasks": [{"ansible.builtin.debug": {"msg": "inspect"}}], "roles": ["repair"]}],
        [{"hosts": "localhost", "post_tasks": [{"ansible.builtin.assert": {"that": "service_ready"}}]}],
        [{"import_playbook": "repair.yml"}],
        [{"hosts": "database", "roles": ["repair"]}, {"hosts": "frontend", "roles": ["verify"]}],
    ],
)
async def test_als_can_choose_modules_roles_blocks_and_multiple_plays(generated):
    outcome, generation, invoke = await _run_node(als_return={"response": yaml.safe_dump(generated)})
    assert outcome["remediation_result"].success is True
    generation.assert_awaited_once()
    committed = next(
        call.args[1]["playbook_content"] for call in invoke.call_args_list if call.args[0] == "commit_playbook"
    )
    assert yaml.safe_load(committed) == generated


@pytest.mark.parametrize("generated", ["[]", "not: [valid: yaml: {{", "[42]", "- tasks: []", "- hosts: localhost"])
async def test_malformed_output_corrections_remain_bounded_and_do_not_publish(generated):
    outcome, generation, invoke = await _run_node(als_return={"response": generated})
    assert outcome["remediation_result"].success is False
    assert generation.await_count == 2
    first_prompt, first_attachments = generation.call_args_list[0].args
    correction_prompt, correction_attachments = generation.call_args_list[1].args
    assert first_prompt in correction_prompt
    assert RESTART_VERIFICATION not in correction_prompt
    assert correction_attachments == first_attachments
    invoke.assert_not_called()


async def test_correction_preserves_the_request_and_publishes_valid_output():
    outcome, generation, invoke = await _run_node(als_side_effect=[{"response": "[]"}, _ALS_RESPONSE])
    assert outcome["remediation_result"].success is True
    assert generation.await_count == 2
    commits = [call for call in invoke.call_args_list if call.args[0] == "commit_playbook"]
    assert len(commits) == 1
    assert yaml.safe_load(commits[0].args[1]["playbook_content"]) == restart_playbook()


async def test_yaml_parser_error_reaches_correction_prompt_before_native_validation():
    invalid_yaml = (
        "- hosts: localhost\n  tasks:\n  - ansible.builtin.assert:\n      that:\n"
        "        - current == previous | combine({'resources': {'limits': {'cpu': desired}}}, recursive=true)\n"
    )
    outcome, generation, invoke = await _run_node(als_side_effect=[{"response": invalid_yaml}, _ALS_RESPONSE])

    assert outcome["remediation_result"].success is True
    correction_prompt = generation.call_args_list[1].args[0]
    assert "Invalid YAML:" in correction_prompt
    assert "line 5" in correction_prompt
    assert "expected <block end>, but found '}'" in correction_prompt
    assert "Expected a non-empty YAML list" not in correction_prompt
    validations = [call for call in invoke.call_args_list if call.args[0] == "validate_playbook"]
    assert len(validations) == 1
    assert validations[0].args[1]["playbook_content"] == _ALS_RESPONSE["response"].strip()


async def test_native_syntax_error_is_corrected_before_publishing_exact_validated_yaml():
    invalid_yaml = "- hosts: localhost\n  tasks:\n  - kubernetes.core.k8s_patch: {}"
    valid_yaml = "# Keep this exact validated text\n" + _ALS_RESPONSE["response"]
    diagnostic = "couldn't resolve module/action 'kubernetes.core.k8s_patch'"

    async def validate_then_invoke(tool_name, kwargs):
        if tool_name == "validate_playbook" and kwargs["playbook_content"] == invalid_yaml:
            return {"success": True, "valid": False, "error": diagnostic}
        return await _default_invoke(tool_name, kwargs)

    outcome, generation, invoke = await _run_node(
        als_side_effect=[{"response": invalid_yaml}, {"response": valid_yaml}], invoke_fn=validate_then_invoke
    )
    assert outcome["remediation_result"].success is True
    assert generation.await_count == 2
    assert diagnostic in generation.call_args_list[1].args[0]
    calls = [(call.args[0], call.args[1]) for call in invoke.call_args_list]
    assert [tool for tool, _ in calls[:3]] == ["validate_playbook", "validate_playbook", "commit_playbook"]
    assert calls[1][1]["playbook_content"] == calls[2][1]["playbook_content"] == valid_yaml.strip()
    assert calls[1][1]["base_template_name"] == "lightspeed-runner"
    launch_vars = next(kwargs["extra_vars"] for tool, kwargs in calls if tool == "launch_job")
    assert calls[1][1]["extra_vars"] == launch_vars
    assert all(
        call.kwargs
        == ({"timeout_seconds": PLAYBOOK_VALIDATION_TIMEOUT_SECONDS} if call.args[0] == "validate_playbook" else {})
        for call in invoke.call_args_list
    )


async def test_duplicate_keys_reach_native_validator_without_being_silently_removed():
    duplicate_yaml = "- hosts: localhost\n  tasks:\n  - debug:\n      msg: first\n      msg: second"
    diagnostic = "found a duplicate dict key (msg)"
    validator = AsyncMock(return_value={"success": True, "valid": False, "error": diagnostic})
    outcome, generation, invoke = await _run_node(als_return={"response": duplicate_yaml}, invoke_fn=validator)
    assert outcome["remediation_result"].success is False
    assert generation.await_count == 2
    assert all(call.args[0] == "validate_playbook" for call in invoke.call_args_list)
    assert all(call.args[1]["playbook_content"] == duplicate_yaml for call in invoke.call_args_list)
    assert diagnostic in generation.call_args_list[1].args[0]


@pytest.mark.parametrize(
    "validation_reply",
    [
        {"success": False, "error": "Execution environment unavailable"},
        {"success": True},
        {"success": True, "valid": "true"},
    ],
)
async def test_validator_unavailable_or_malformed_stops_without_als_retry(validation_reply):
    validator = AsyncMock(return_value=validation_reply)
    outcome, generation, invoke = await _run_node(als_return=_ALS_RESPONSE, invoke_fn=validator)
    remediation = outcome["remediation_result"]
    assert remediation.success is False
    assert remediation.job_id == ""
    assert remediation.lightspeed_conversation_id == "conv-123"
    assert "validation unavailable" in remediation.output_summary.lower()
    generation.assert_awaited_once()
    assert [call.args[0] for call in invoke.call_args_list] == ["validate_playbook"]


async def test_validator_transport_failure_stops_without_als_retry():
    validator = AsyncMock(side_effect=httpx.ConnectError("Validator unreachable"))
    outcome, generation, invoke = await _run_node(als_return=_ALS_RESPONSE, invoke_fn=validator)
    remediation = outcome["remediation_result"]
    assert remediation.success is False
    assert remediation.lightspeed_conversation_id == "conv-123"
    assert "Validator unreachable" in remediation.output_summary
    generation.assert_awaited_once()
    assert [call.args[0] for call in invoke.call_args_list] == ["validate_playbook"]


async def test_different_native_errors_exhaust_retries_without_launch():
    errors = [f"Invalid module {index}" for index in range(3)]
    validator = AsyncMock(side_effect=[{"success": True, "valid": False, "error": error} for error in errors])
    outcome, generation, invoke = await _run_node(als_return=_ALS_RESPONSE, invoke_fn=validator)
    assert outcome["remediation_result"].success is False
    assert generation.await_count == 3
    assert [call.args[0] for call in invoke.call_args_list] == ["validate_playbook"] * 3
    assert errors[0] in generation.call_args_list[1].args[0]
    assert errors[1] in generation.call_args_list[2].args[0]


async def test_correction_feedback_is_bounded_without_truncating_validation_input():
    from agent_service.nodes.lightspeed import (
        _MAX_ERROR_SUMMARY_CHARS,
        _MAX_REJECTED_RESPONSE_CHARS,
    )

    rejected_yaml = _ALS_RESPONSE["response"] + "\n# " + "x" * _MAX_REJECTED_RESPONSE_CHARS + " SOURCE_END"
    diagnostic = "e" * _MAX_ERROR_SUMMARY_CHARS + " DIAGNOSTIC_END"
    validator = AsyncMock(return_value={"success": True, "valid": False, "error": diagnostic})
    _, generation, invoke = await _run_node(als_return={"response": rejected_yaml}, invoke_fn=validator)
    correction_prompt = generation.call_args_list[1].args[0]
    assert "SOURCE_END" not in correction_prompt
    assert "DIAGNOSTIC_END" not in correction_prompt
    assert invoke.call_args_list[0].args[1]["playbook_content"] == rejected_yaml


async def test_three_different_format_errors_exhaust_generation_without_launch():
    outcome, generation, invoke = await _run_node(
        als_side_effect=[{"response": "[]"}, {"response": "- name: Missing hosts"}, {"response": "- hosts: localhost"}]
    )
    assert outcome["remediation_result"].success is False
    assert generation.await_count == 3
    invoke.assert_not_called()


async def test_jinja_quoting_preserves_the_generated_playbook():
    candidate = restart_playbook()
    generated_yaml = yaml.safe_dump(candidate).replace("'{{ deployment_name }}'", "{{ deployment_name }}")
    assert generated_yaml != yaml.safe_dump(candidate)
    outcome, generation, invoke = await _run_node(als_return={"response": generated_yaml})
    assert outcome["remediation_result"].success is True
    generation.assert_awaited_once()
    committed = next(
        call.args[1]["playbook_content"] for call in invoke.call_args_list if call.args[0] == "commit_playbook"
    )
    assert yaml.safe_load(committed) == candidate


async def test_essential_evidence_survives_supplementary_budget():
    long_logs = RESTART_EVIDENCE + "\n" + "ordinary log\n" * 2000
    outcome, generation, _ = await _run_node(als_return=_ALS_RESPONSE, pod_logs=long_logs)
    assert outcome["remediation_result"].success is True
    prompt, attachments = generation.call_args.args
    assert RESTART_EVIDENCE in prompt
    assert all(RESTART_EVIDENCE not in attachment["content"] for attachment in attachments)


def test_large_json_attachment_remains_valid_and_marks_omission():
    import json

    original = {
        "attachment_type": "configuration",
        "content_type": "application/json",
        "content": json.dumps({"events": ["event" * 2000]}),
    }
    attachments = _build_attachments(None, None, extra_attachments=[original])
    assert "omitted" in json.loads(attachments[0]["content"])
    assert json.loads(original["content"])["events"]


async def test_generation_does_not_call_a_summarizer_or_use_it_as_fallback():
    with patch("agent_service.config.get_llm", side_effect=RuntimeError("summarizer unavailable")) as get_llm:
        accepted, _, _ = await _run_node(als_return=_ALS_RESPONSE)
        rejected, generation, _ = await _run_node(als_return=_ALS_RESPONSE, rca=make_rca())
    assert accepted["remediation_result"].success is True
    assert rejected["remediation_result"].success is False
    generation.assert_not_called()
    get_llm.assert_not_called()


async def test_disabled_als_is_failure_not_success():
    with patch("agent_service.nodes.lightspeed.LIGHTSPEED_URL", ""):
        outcome = await lightspeed_node(selected_state())
    assert outcome["remediation_result"].success is False


async def test_generation_validation_failure_has_no_aap_job():
    outcome, _, invoke = await _run_node(
        als_return={"response": "not: [valid: yaml: {{", "conversation_id": "conv-invalid"}
    )
    remediation = outcome["remediation_result"]
    assert not remediation.success
    assert remediation.job_id == ""
    assert remediation.lightspeed_conversation_id == "conv-invalid"
    assert "validation" in remediation.output_summary
    invoke.assert_not_called()


async def test_als_refusal_retains_reason_and_conversation_without_launch():
    refusal = "Cannot generate the remediation because the DNS upstream address is missing"
    outcome, generation, invoke = await _run_node(
        als_return={"response": refusal, "conversation_id": "conv-missing-input"}
    )
    remediation = outcome["remediation_result"]
    assert not remediation.success
    assert refusal in remediation.output_summary
    assert "Expected a non-empty YAML list of plays" in remediation.output_summary
    assert remediation.lightspeed_conversation_id == "conv-missing-input"
    assert remediation.job_id == ""
    assert generation.await_count == 2
    invoke.assert_not_called()


async def test_aap_monitoring_exception_preserves_launched_job_id():
    with patch("agent_service.nodes.lightspeed.evaluate_job", side_effect=RuntimeError("monitor unavailable")):
        outcome, _, _ = await _run_node(als_return=_ALS_RESPONSE)
    remediation = outcome["remediation_result"]
    assert not remediation.success
    assert remediation.job_id == "42"
    assert remediation.lightspeed_conversation_id == "conv-123"
    assert "monitoring failed" in remediation.output_summary
    assert "monitor unavailable" in remediation.output_summary
    assert remediation.tool_used == "aap"
    assert remediation.action_taken == remediation.generated_playbook_name
