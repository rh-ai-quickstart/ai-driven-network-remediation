from unittest.mock import AsyncMock, patch

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
    lightspeed_node,
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


def _state(rca=None, log_event=None, use_defaults=True):
    return IncidentState(
        raw_event="test event",
        root_cause_analysis=make_rca() if use_defaults and rca is None else rca,
        log_event=make_log_event() if use_defaults and log_event is None else log_event,
    )


async def _default_invoke(tool_name, kwargs):
    if tool_name == "upsert_job_template":
        return _UPSERT_OK
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
    invoke_mock = AsyncMock(side_effect=invoke_fn)
    with (
        patch("agent_service.nodes.lightspeed.LIGHTSPEED_URL", "https://als-stub"),
        patch("agent_service.nodes.lightspeed._call_als", als_mock),
        patch("agent_service.nodes.lightspeed._invoke_tool", invoke_mock),
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
        assert invoke_mock.call_count == 2

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

    async def test_empty_als_response(self):
        result, _, _ = await _run_node(als_return={"response": ""})
        rr = result["remediation_result"]
        assert rr.success is True
        assert rr.generated_playbook_preview == ""


# -- AAP execution (upsert + launch) --


class TestAAPExecution:
    async def test_upsert_uses_wrapper_playbook(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        upsert_call = invoke_mock.call_args_list[0]
        assert upsert_call[0][0] == "upsert_job_template"
        args = upsert_call[0][1]
        assert args["playbook"] == "playbooks/lightspeed-generate-and-run.yaml"
        assert args["base_template_name"] == "lightspeed-runner"
        assert args["template_name"] == "remediate-oomkilled-nginx-abc123"

    async def test_launch_extra_vars_contain_generated_yaml(self):
        _, _, invoke_mock = await _run_node(als_return=_ALS_RESPONSE)

        launch_call = invoke_mock.call_args_list[1]
        assert launch_call[0][0] == "launch_job"
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["generated_from_model"] is True
        assert extra_vars["generated_playbook_name"] == ("remediate-oomkilled-nginx-abc123")
        assert "hosts: all" in extra_vars["generated_playbook_yaml"]
        assert extra_vars["namespace"] == "prod"
        assert extra_vars["pod_name"] == "nginx-abc123"

    async def test_upsert_failure(self):
        async def upsert_fails(tool_name, kwargs):
            if tool_name == "upsert_job_template":
                return {"success": False, "error": "template conflict"}
            return _LAUNCH_OK

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
            if tool_name == "upsert_job_template":
                return _UPSERT_OK
            return {"success": False, "error": "quota exceeded"}

        result, _, _ = await _run_node(
            als_return=_ALS_RESPONSE,
            invoke_fn=launch_fails,
        )

        rr = result["remediation_result"]
        assert rr.success is False
        assert rr.generated_playbook_name is not None

    async def test_no_log_event_still_has_playbook_vars(self):
        result, _, invoke_mock = await _run_node(
            als_return=_ALS_RESPONSE,
            log_event=None,
            use_defaults=False,
        )

        rr = result["remediation_result"]
        assert rr.success is True

        launch_call = invoke_mock.call_args_list[1]
        extra_vars = launch_call[0][1]["extra_vars"]
        assert extra_vars["generated_from_model"] is True
        assert "hosts: all" in extra_vars["generated_playbook_yaml"]
        assert "namespace" not in extra_vars


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
