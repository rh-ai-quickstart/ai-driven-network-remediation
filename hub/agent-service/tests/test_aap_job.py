import json
from unittest.mock import AsyncMock

import pytest

from agent_service.nodes import _aap_job


async def test_failed_summary_keeps_late_task_and_json_error(monkeypatch):
    task_name = "Wait for Deployment to restart"
    fatal_message = "The conditional check failed: deployment_status.json.generation " "has no attribute generation"
    prior_output = (
        "TASK [Patch Deployment to increase CPU limit] ***\nchanged: [localhost]\n"
        + "ok: [localhost] => completed successfully\n" * 40
    )
    output_text = (
        prior_output
        + f"TASK [{task_name}] ***\nfatal: [localhost]: FAILED! => "
        + json.dumps({"msg": fatal_message, "changed": False})
    )
    monkeypatch.setattr(
        _aap_job,
        "poll_job",
        AsyncMock(
            return_value={
                "status": "failed",
                "failed": True,
                "elapsed": 12,
                "finished": "done",
                "result_traceback": "The error was: error while evaluating conditional",
            }
        ),
    )
    monkeypatch.setattr(_aap_job, "get_job_output", AsyncMock(return_value=output_text))

    outcome = await _aap_job.evaluate_job(1001, timeout=60)

    assert len(prior_output) > 500
    assert outcome.output_summary == f"Task '{task_name}' failed: {fatal_message}"
    assert outcome.success is False
    assert outcome.output_text == output_text


@pytest.mark.parametrize("failure_detail", ["command failed: disk full", '{"msg": unquoted disk full'])
async def test_last_failure_keeps_unparsed_error_and_nearest_task(monkeypatch, failure_detail):
    output_text = (
        "TASK [Earlier task] ***\nfatal: [localhost]: FAILED! => {invalid json\n"
        f"TASK [Final task] ***\nfatal: [localhost]: FAILED! => {failure_detail}"
    )
    monkeypatch.setattr(
        _aap_job,
        "poll_job",
        AsyncMock(return_value={"status": "failed", "failed": True}),
    )
    monkeypatch.setattr(_aap_job, "get_job_output", AsyncMock(return_value=output_text))

    outcome = await _aap_job.evaluate_job(1001, timeout=60)

    assert "Final task" in outcome.output_summary
    assert "Earlier task" not in outcome.output_summary
    assert failure_detail in outcome.output_summary


async def test_unreachable_summary_keeps_connection_error(monkeypatch):
    output_text = (
        "TASK [Check host connection] ***\nfatal: [localhost]: UNREACHABLE! => " '{"msg": "SSH connection refused"}'
    )
    monkeypatch.setattr(
        _aap_job,
        "poll_job",
        AsyncMock(return_value={"status": "failed", "failed": True}),
    )
    monkeypatch.setattr(_aap_job, "get_job_output", AsyncMock(return_value=output_text))

    outcome = await _aap_job.evaluate_job(1001, timeout=60)

    assert outcome.output_summary == "Task 'Check host connection' failed: SSH connection refused"


def test_command_failure_appends_stderr_to_generic_message():
    output_text = (
        "TASK [Run command] ***\nfatal: [localhost]: FAILED! => "
        '{"msg": "non-zero return code", "stderr": "permission denied"}'
    )

    assert _aap_job._failure_summary(output_text) == (
        "Task 'Run command' failed: non-zero return code: permission denied"
    )


@pytest.mark.parametrize("job_status", ["failed", "error", "canceled"])
async def test_failed_terminal_status_without_details_uses_job_fallback(monkeypatch, job_status):
    monkeypatch.setattr(
        _aap_job,
        "poll_job",
        AsyncMock(return_value={"status": job_status, "failed": False}),
    )
    monkeypatch.setattr(_aap_job, "get_job_output", AsyncMock(return_value=""))

    outcome = await _aap_job.evaluate_job(1001, timeout=60)

    assert outcome.success is False
    assert outcome.timed_out is False
    assert outcome.output_summary == f"Job 1001 {job_status}"
