"""Shared base class for ServiceNow REST API clients.

Centralises session initialisation, authentication, and common
table-lookup helpers so the individual automation modules stay
DRY.
"""

from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from .utils import get_env_var

DEFAULT_TIMEOUT_SECONDS = 30


class _TimeoutAdapter(HTTPAdapter):
    """HTTPAdapter that injects a default timeout on every request."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SECONDS, **kwargs):  # type: ignore[no-untyped-def]
        self._timeout = timeout
        super().__init__(**kwargs)

    def send(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("timeout", self._timeout)
        return super().send(*args, **kwargs)


class ServiceNowClient:
    """Thin wrapper around a ``requests.Session`` pre-configured for
    a ServiceNow instance.

    Parameters
    ----------
    username, password:
        Override the default environment-variable credentials
        (``SERVICENOW_USERNAME`` / ``SERVICENOW_PASSWORD``).
        Useful for validating as a freshly created agent user
        rather than admin.
    timeout:
        Per-request timeout in seconds.  Applies to both connect
        and read phases.  Defaults to ``DEFAULT_TIMEOUT_SECONDS``.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.instance_url: str = get_env_var("SERVICENOW_INSTANCE_URL").rstrip("/")
        self.username: str = username or get_env_var("SERVICENOW_USERNAME")
        self.password: str = password or get_env_var("SERVICENOW_PASSWORD")

        self.session = requests.Session()
        self.session.auth = (self.username, self.password)
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
        adapter = _TimeoutAdapter(timeout=timeout)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def get_user_sys_id(self, user_id: str) -> str:
        """Look up the ``sys_id`` for a ``sys_user`` by ``user_name``.

        Raises ``ValueError`` when the user is not found and lets
        ``requests.RequestException`` propagate on transport/HTTP errors.
        """
        url = f"{self.instance_url}/api/now/table/sys_user"
        params = {"sysparm_query": f"user_name={user_id}", "sysparm_fields": "sys_id"}

        response = self.session.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("result"):
            return str(data["result"][0]["sys_id"])
        raise ValueError(f"User '{user_id}' not found")
