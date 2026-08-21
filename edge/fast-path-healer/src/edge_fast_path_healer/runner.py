from __future__ import annotations

import os
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Response

from edge_fast_path_healer.k8s_client import load_k8s_apps_api
from edge_fast_path_healer.models import RemediationEvent, RemediationResult
from edge_fast_path_healer.remediate import remediate_oom


@dataclass(frozen=True)
class RunnerSettings:
    namespace: str
    deployment: str
    site_id: str
    memory_request: str
    memory_limit: str
    cooldown_seconds: int


def settings_from_env() -> RunnerSettings:
    return RunnerSettings(
        namespace=os.environ["EDGE_NAMESPACE"],
        deployment=os.environ["EDGE_DEPLOYMENT"],
        site_id=os.environ["EDGE_SITE_ID"],
        memory_request=os.environ.get("EDGE_MEMORY_REQUEST", "64Mi"),
        memory_limit=os.environ.get("EDGE_MEMORY_LIMIT", "128Mi"),
        cooldown_seconds=int(os.environ.get("EDGE_COOLDOWN_SECONDS", "300")),
    )


def create_app(settings: RunnerSettings) -> FastAPI:
    app = FastAPI(title="edge-fast-path-runner", docs_url=None, redoc_url=None)
    api = None

    def get_api():
        nonlocal api
        if api is None:
            api = load_k8s_apps_api()
        return api

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/endpoint")
    def endpoint(event: RemediationEvent, response: Response) -> RemediationResult | dict:
        if event.namespace != settings.namespace:
            response.status_code = 202
            return {"ignored": True, "reason": "namespace mismatch"}
        if "oomkilled" not in event.failure_type.lower():
            response.status_code = 202
            return {"ignored": True, "reason": "failure_type not matched"}
        if event.deployment != settings.deployment:
            response.status_code = 202
            return {"ignored": True, "reason": "deployment mismatch"}

        result = remediate_oom(
            get_api(),
            namespace=settings.namespace,
            deployment=settings.deployment,
            site_id=settings.site_id,
            memory_request=settings.memory_request,
            memory_limit=settings.memory_limit,
            cooldown_seconds=settings.cooldown_seconds,
        )
        if result.result == "failed":
            response.status_code = 500
        return result

    return app


def main() -> None:
    settings = settings_from_env()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
