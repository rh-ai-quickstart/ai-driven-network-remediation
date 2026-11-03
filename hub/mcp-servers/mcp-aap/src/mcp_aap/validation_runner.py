"""Credential-free entry point copied into an execution-environment validation Job."""

import base64
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path

SYNTAX_TIMEOUT_SECONDS = 20
MAX_DIAGNOSTIC_BYTES = 8000


def _check(directory: Path, playbook: str, extra_vars: str) -> dict:
    (directory / "playbook.yaml").write_text(playbook)
    (directory / "extra_vars.json").write_text(extra_vars)
    (directory / "ansible.cfg").write_text("[defaults]\nduplicate_dict_key = error\n")
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(directory),
        "ANSIBLE_CONFIG": str(directory / "ansible.cfg"),
        "ANSIBLE_LOCAL_TEMP": str(directory / "tmp"),
        "ANSIBLE_DUPLICATE_YAML_DICT_KEY": "error",
        "ANSIBLE_NOCOLOR": "1",
    }
    command = ["ansible-playbook", "--syntax-check", "-i", "localhost,", "-e", "@extra_vars.json", "playbook.yaml"]
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=directory,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return _collect(process, output)


def _collect(process, output) -> dict:
    try:
        exit_code = process.wait(timeout=SYNTAX_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Ansible syntax check timed out"}
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    if exit_code < 0:
        return {"success": False, "error": f"Ansible syntax process terminated by signal {-exit_code}"}
    output.seek(0)
    diagnostic = output.read(MAX_DIAGNOSTIC_BYTES + 1)
    return {
        "success": True,
        "valid": exit_code == 0,
        "error": diagnostic[:MAX_DIAGNOSTIC_BYTES].decode(errors="replace"),
        "diagnostic_truncated": len(diagnostic) > MAX_DIAGNOSTIC_BYTES,
    }


def main() -> None:
    try:
        playbook = base64.b64decode(os.environ.pop("PLAYBOOK_BASE64")).decode()
        extra_vars = base64.b64decode(os.environ.pop("EXTRA_VARS_BASE64")).decode()
        with tempfile.TemporaryDirectory() as directory:
            outcome = _check(Path(directory), playbook, extra_vars)
    except (OSError, ValueError, KeyError) as exc:
        outcome = {"success": False, "error": f"Validation runtime unavailable: {exc}"}
    print(json.dumps(outcome), flush=True)


if __name__ == "__main__":
    main()
