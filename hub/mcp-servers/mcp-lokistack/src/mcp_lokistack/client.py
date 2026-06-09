"""LokiStack HTTP client with tenant-aware routing and retry logic."""

import atexit
import threading
import time

import httpx
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from . import config

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_lock = threading.Lock()
_clients: dict[tuple[str, str], tuple[httpx.Client, float]] = {}
_CLIENT_TTL = 300


def _base_url_for(service: str, tenant: str) -> str:
    if service == "logs":
        return f"{config.LOKI_URL}/api/logs/v1/{tenant}/loki/api/v1"
    if service == "ruler":
        return f"{config.LOKI_URL}/loki/api/v1"
    raise ValueError(f"Unknown service: {service!r}")


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout))


def _tls_verify() -> bool | str:
    if config.LOKI_CA_CERT_PATH:
        return config.LOKI_CA_CERT_PATH
    return config.LOKI_TLS_VERIFY


def _build_client(service: str, tenant: str) -> httpx.Client:
    base_url = _base_url_for(service, tenant)
    headers: dict[str, str] = {}
    token = config.read_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=config.LOKI_QUERY_TIMEOUT,
        verify=_tls_verify(),
    )


def _get_client(service: str, tenant: str) -> httpx.Client:
    key = (service, tenant)
    now = time.monotonic()
    with _lock:
        entry = _clients.get(key)
        if entry is not None:
            client, created = entry
            if now - created < _CLIENT_TTL:
                return client
            client.close()
        client = _build_client(service, tenant)
        _clients[key] = (client, now)
        return client


def _invalidate_client(service: str, tenant: str) -> None:
    key = (service, tenant)
    with _lock:
        entry = _clients.pop(key, None)
        if entry is not None:
            entry[0].close()


def _close_all() -> None:
    with _lock:
        for client, _ in _clients.values():
            client.close()
        _clients.clear()


atexit.register(_close_all)


def _execute_with_retry(service: str, tenant: str, fn):
    for attempt in Retrying(
        stop=stop_after_attempt(config.LOKI_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    ):
        with attempt:
            try:
                client = _get_client(service, tenant)
                return fn(client)
            except (httpx.ConnectError, httpx.ReadTimeout):
                _invalidate_client(service, tenant)
                raise


def _loki_get(service: str, tenant: str, path: str, params: dict) -> dict:
    def _call(client: httpx.Client) -> dict:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    return _execute_with_retry(service, tenant, _call)


def loki_query_range(tenant: str, params: dict) -> dict:
    return _loki_get("logs", tenant, "/query_range", params)


def loki_query(tenant: str, params: dict) -> dict:
    return _loki_get("logs", tenant, "/query", params)


def loki_label_values(tenant: str, label: str, params: dict) -> dict:
    return _loki_get("logs", tenant, f"/label/{label}/values", params)


def get_label_values(tenant: str, label: str) -> list[str]:
    return loki_label_values(tenant, label, {}).get("data", [])
