import re

import yaml
from loguru import logger

_UNQUOTED_JINJA_RE = re.compile(
    r"^(\s*(?:- )?[\w.\- ]+:[ \t]+)((?:.*\{\{.*?\}\}.*?))[ \t]*$",
    re.MULTILINE,
)

_STRATEGIC_MERGE_PATCH = "application/strategic-merge-patch+json"
_JSON_PATCH = "application/json-patch+json"
_APPLY_PATCH = "application/apply-patch+yaml"
_FIELD_MANAGER_RE = re.compile(r"[?&]fieldManager=")

_TASK_LIST_KEYS = ("tasks", "pre_tasks", "post_tasks", "handlers")
_BLOCK_KEYS = ("block", "rescue", "always")

_DANGEROUS_HEADER_PREFIXES = ("impersonate-",)
_DANGEROUS_HEADERS = frozenset({"x-forwarded-for", "x-real-ip"})

_K8S_HOST_LOOKUP_RE = re.compile(
    r"\{\{\s*lookup\s*\(\s*['\"]env['\"]\s*,\s*['\"]K8S_AUTH_HOST['\"]\s*\)\s*\}\}"
)
_K8S_KEY_LOOKUP_RE = re.compile(
    r"\{\{\s*lookup\s*\(\s*['\"]env['\"]\s*,\s*['\"]K8S_AUTH_API_KEY['\"]\s*\)\s*\}\}"
)
_BASIC_AUTH_KEYS = ("url_username", "url_password", "force_basic_auth")
_DEPLOYMENT_URL_RE = re.compile(r"/deployments/")

_ANSIBLE_DATETIME_ISO_RE = re.compile(r"\{\{\s*ansible_date_time\.iso8601\s*\}\}")
_NOW_UTC_EXPR = "{{ now(utc=True, fmt='%Y-%m-%dT%H:%M:%SZ') }}"


def fix_ansible_facts(yaml_text: str) -> str:
    """Replace gather_facts-dependent expressions with fact-free equivalents."""
    return _ANSIBLE_DATETIME_ISO_RE.sub(_NOW_UTC_EXPR, yaml_text)


def _quote_jinja(yaml_text: str) -> str:
    """Quote bare Jinja2 expressions so the YAML is valid for Ansible."""
    def _replacer(m):
        prefix, value = m.group(1), m.group(2)
        if value.startswith('"') or value.startswith("'"):
            return m.group(0)
        return f'{prefix}"{value}"'
    return _UNQUOTED_JINJA_RE.sub(_replacer, yaml_text)


def _walk_tasks(task_list):
    """Yield (task_dict, uri_dict) from a task list, recursing into block/rescue/always."""
    for task in task_list or []:
        if not isinstance(task, dict):
            continue
        uri = task.get("uri") or task.get("ansible.builtin.uri")
        if isinstance(uri, dict):
            yield task, uri
        for block_key in _BLOCK_KEYS:
            sub_tasks = task.get(block_key)
            if isinstance(sub_tasks, list):
                yield from _walk_tasks(sub_tasks)


def _iter_uri_tasks(play: dict):
    """Yield (task_dict, uri_dict) for every uri task in a play."""
    for section_key in _TASK_LIST_KEYS:
        yield from _walk_tasks(play.get(section_key))


def _iter_plays(parsed: list | dict | None):
    """Yield each play dict, or nothing if parsed is not a play list."""
    if not isinstance(parsed, list):
        return
    for play in parsed:
        if isinstance(play, dict):
            yield play


def _is_dangerous_header(header_name: str) -> bool:
    lower = header_name.lower()
    if lower in _DANGEROUS_HEADERS:
        return True
    return any(lower.startswith(prefix) for prefix in _DANGEROUS_HEADER_PREFIXES)


def _strip_headers_from_uri(uri: dict) -> None:
    """Remove dangerous headers from a single uri task's headers dict."""
    headers = uri.get("headers")
    if not isinstance(headers, dict):
        return
    dangerous_keys = [name for name in headers if _is_dangerous_header(name)]
    for header_name in dangerous_keys:
        logger.warning(f"Stripped dangerous header from generated playbook: {header_name}")
        del headers[header_name]


def _strip_dangerous_headers(parsed: list | dict | None) -> list | dict | None:
    """Strip headers that could exploit cluster-proxy impersonation (CVE-2026-17107)."""
    for play in _iter_plays(parsed):
        for _task, uri in _iter_uri_tasks(play):
            _strip_headers_from_uri(uri)
    return parsed


def _rewrite_play_vars(play_vars: dict) -> None:
    """Replace K8S_AUTH env-var lookups in play-level vars with cluster-proxy variables."""
    for var_name, var_value in list(play_vars.items()):
        if not isinstance(var_value, str):
            continue
        if "K8S_AUTH_HOST" in var_value:
            play_vars[var_name] = "{{ hub_url }}/{{ edge_site_id }}"
        elif "K8S_AUTH_API_KEY" in var_value:
            play_vars[var_name] = "{{ token_acm }}"


def _fix_uri_auth(task: dict, uri: dict) -> None:
    """Rewrite a single uri task's auth to use cluster-proxy variables."""
    task.pop("env", None)
    task.pop("environment", None)

    url = uri.get("url", "")
    if isinstance(url, str) and "K8S_AUTH_HOST" in url:
        url = _K8S_HOST_LOOKUP_RE.sub("{{ hub_url }}/{{ edge_site_id }}", url)
        uri["url"] = url

    for auth_key in _BASIC_AUTH_KEYS:
        uri.pop(auth_key, None)

    uri.setdefault("validate_certs", False)

    is_hub_url = "{{ hub_url }}" in str(url)

    headers = uri.get("headers", {})
    if not isinstance(headers, dict):
        return
    auth_header = headers.get("Authorization", "")
    if is_hub_url and isinstance(auth_header, str) and "K8S_AUTH_API_KEY" in auth_header:
        headers["Authorization"] = _K8S_KEY_LOOKUP_RE.sub("{{ token_acm }}", auth_header)
    if is_hub_url and "Authorization" not in headers:
        headers["Authorization"] = "Bearer {{ token_acm }}"
    uri["headers"] = headers


def _fix_cluster_proxy_auth(parsed: list | dict | None) -> list | dict | None:
    """Rewrite K8S_AUTH_HOST/K8S_AUTH_API_KEY to cluster-proxy variables."""
    for play in _iter_plays(parsed):
        play_vars = play.get("vars")
        if isinstance(play_vars, dict):
            _rewrite_play_vars(play_vars)
        for task, uri in _iter_uri_tasks(play):
            _fix_uri_auth(task, uri)
    return parsed


def _wrap_deployment_body(uri: dict) -> None:
    """Move spec.containers under spec.template.spec for Deployment patches."""
    body = uri.get("body")
    if not isinstance(body, dict):
        return
    url = str(uri.get("url", ""))
    if not _DEPLOYMENT_URL_RE.search(url):
        return
    spec = body.get("spec")
    if not isinstance(spec, dict):
        return
    if "containers" in spec and "template" not in spec:
        spec["template"] = {"spec": {"containers": spec.pop("containers")}}


def _is_server_side_apply(uri: dict) -> bool:
    """Detect a server-side apply PATCH so its Content-Type is left intact."""
    headers = uri.get("headers")
    if isinstance(headers, dict) and "apply-patch" in str(headers.get("Content-Type", "")).lower():
        return True
    return bool(_FIELD_MANAGER_RE.search(str(uri.get("url", ""))))


def _fix_patch_uri(uri: dict) -> None:
    """Fix body_format, Content-Type, status_code, and body path on a PATCH uri task."""
    if isinstance(uri.get("body"), str):
        try:
            uri["body"] = yaml.safe_load(uri["body"])
        except yaml.YAMLError:
            pass

    if isinstance(uri.get("body"), (dict, list)):
        uri["body_format"] = "json"

    uri.pop("content_type", None)

    _wrap_deployment_body(uri)

    headers = uri.setdefault("headers", {})
    if isinstance(headers, dict):
        if _is_server_side_apply(uri):
            headers.setdefault("Content-Type", _APPLY_PATCH)
        elif isinstance(uri.get("body"), list):
            headers["Content-Type"] = _JSON_PATCH
        else:
            headers["Content-Type"] = _STRATEGIC_MERGE_PATCH

    if "status_code" not in uri:
        uri["status_code"] = 200


def _fix_patch_tasks(parsed: list | dict | None) -> list | dict | None:
    """Post-process parsed playbook YAML to fix PATCH tasks."""
    for play in _iter_plays(parsed):
        for _task, uri in _iter_uri_tasks(play):
            if str(uri.get("method", "")).upper() == "PATCH":
                _fix_patch_uri(uri)
    return parsed


def sanitize_playbook(parsed: list | dict | None) -> list | dict | None:
    """Rewrite ALS YAML: cluster-proxy auth, dangerous headers, then PATCH fixes."""
    # A single play may arrive as a bare dict; wrap it so the play iterators run.
    if isinstance(parsed, dict):
        parsed = [parsed]
    parsed = _fix_cluster_proxy_auth(parsed)
    parsed = _strip_dangerous_headers(parsed)
    parsed = _fix_patch_tasks(parsed)
    return parsed
