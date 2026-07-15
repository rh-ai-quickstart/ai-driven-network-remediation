import os
import time

import httpx
import pytest


def sync_runbooks(ingestion_client):
    response = ingestion_client.post("/runbooks/sync", timeout=30.0)
    assert response.status_code == 200
    return response.json()


def wait_for_agent_ready() -> None:
    """Wait until agent-service reports Kafka consumer connected (PR #105 ready gate)."""
    timeout_s = int(os.environ.get("AGENT_READY_TIMEOUT_SECONDS", "120"))
    base_url = os.environ.get("AGENT_SERVICE_URL", "http://localhost:8007")
    deadline = time.monotonic() + timeout_s
    last_status: int | None = None
    last_body = ""

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        while time.monotonic() < deadline:
            response = client.get("/ready")
            last_status = response.status_code
            last_body = response.text
            if response.status_code == 200 and response.json().get("ready") is True:
                return
            time.sleep(2)

    pytest.fail(
        f"agent-service not ready within {timeout_s}s "
        f"(last /ready status={last_status}, body={last_body})"
    )
