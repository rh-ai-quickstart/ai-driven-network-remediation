"""Temporary CI comparison of runner, pod, and LlamaStack inference paths."""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REQUEST_TIMEOUT_SECONDS = 45
POD_EXEC_OVERHEAD_SECONDS = 10
POD_TIMEOUT_SECONDS = 2 * REQUEST_TIMEOUT_SECONDS + POD_EXEC_OVERHEAD_SECONDS
MAX_COMPLETION_TOKENS = 128
PROMPT = "Reply with exactly: inference probe ok"
SETTINGS_NAMES = ("ADNR_LLM_URL", "ADNR_LLM_TOKEN", "ADNR_LLM_ID")
STACK_BASE_URL = "http://localhost:8321/v1"
FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter", "function_call")
POD_EXEC_COMMAND = (
    "oc",
    "exec",
    "-i",
    "-n",
    "hub",
    "deployment/llamastack",
    "-c",
    "llama-stack",
    "--",
    "python3",
    "-c",
)


def completion_request(base_url, token, model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_COMPLETION_TOKENS,
        "temperature": 0,
        "stream": False,
    }
    return urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def probe(label, base_url, token, model):
    report = {"probe": label, "tls_verify": base_url.startswith("https://")}
    started = time.monotonic()
    try:
        request = completion_request(base_url, token, model)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            report["http_status"] = response.status
            report.update(completion_metadata(json.load(response)))
    except Exception as error:
        report.update(ok=False, error_type=type(error).__name__)
        if isinstance(error, urllib.error.HTTPError):
            report["http_status"] = error.code
        elif isinstance(error, urllib.error.URLError):
            report["reason_type"] = type(error.reason).__name__
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(report), flush=True)
    return report.get("ok", False)


def completion_metadata(completion):
    choice = completion["choices"][0]
    content = choice["message"].get("content") or ""
    finish_reason = choice.get("finish_reason")
    # Never emit response text, headers, URLs, tokens, or exception messages.
    return {
        "ok": isinstance(content, str) and bool(content.strip()),
        "content_chars": len(content) if isinstance(content, str) else 0,
        "finish_reason": finish_reason if finish_reason in FINISH_REASONS else "unknown",
    }


def direct_probe(label, settings):
    base_url = settings["ADNR_LLM_URL"].rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return probe(label, base_url, settings["ADNR_LLM_TOKEN"], settings["ADNR_LLM_ID"])


def pod_probes(settings):
    direct_ok = direct_probe("pod-direct-maas", settings)
    stack_ok = probe(
        "pod-through-llamastack",
        STACK_BASE_URL,
        "unused",
        "adnr-llm/" + settings["ADNR_LLM_ID"],
    )
    return direct_ok and stack_ok


def run_pod_probes(settings):
    # Credentials travel over stdin, never in the process arguments or logs.
    command = [*POD_EXEC_COMMAND, Path(__file__).read_text(), "pod"]
    try:
        pod_process = subprocess.run(
            command,
            input=json.dumps(settings),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=POD_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"probe": "pod-exec", "ok": False, "error_type": type(error).__name__}), flush=True)
        return False
    if pod_process.returncode:
        print(json.dumps({"probe": "pod-exec", "ok": False, "exit_code": pod_process.returncode}), flush=True)
    return pod_process.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("runner", "pod-exec", "pod"))
    mode = parser.parse_args().mode
    if mode == "pod":
        return pod_probes(json.load(sys.stdin))
    settings = {name: os.environ.get(name, "") for name in SETTINGS_NAMES}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        print(json.dumps({"probe": "configuration", "ok": False, "missing": missing}), flush=True)
        return False
    if mode == "runner":
        return direct_probe("runner-direct-maas", settings)
    return run_pod_probes(settings)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
