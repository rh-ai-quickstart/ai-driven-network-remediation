import re

import yaml
from loguru import logger

from agent_service.config import DEPLOYMENT_MODE

_UNQUOTED_JINJA_RE = re.compile(
    r"^(\s*(?:- )?[\w.\- ]+:[ \t]+)((?:.*\{\{.*?\}\}.*?))[ \t]*$",
    re.MULTILINE,
)

_STRATEGIC_MERGE_PATCH = "application/strategic-merge-patch+json"
_JSON_PATCH = "application/json-patch+json"
_APPLY_PATCH = "application/apply-patch+yaml"
_FIELD_MANAGER_RE = re.compile(r"[?&]fieldManager=")

_TASK_LIST_KEYS = ("pre_tasks", "tasks", "post_tasks", "handlers")
_BLOCK_KEYS = ("block", "rescue", "always")

_DANGEROUS_HEADER_PREFIXES = ("impersonate-",)
_DANGEROUS_HEADERS = frozenset({"x-forwarded-for", "x-real-ip"})

_K8S_HOST_LOOKUP_RE = re.compile(r"\{\{\s*lookup\s*\(\s*['\"]env['\"]\s*,\s*['\"]K8S_AUTH_HOST['\"]\s*\)\s*\}\}")
_PREFIXED_K8S_HOST_RE = re.compile(r"https?://(" + _K8S_HOST_LOOKUP_RE.pattern + r")")
_K8S_KEY_LOOKUP_RE = re.compile(r"\{\{\s*lookup\s*\(\s*['\"]env['\"]\s*,\s*['\"]K8S_AUTH_API_KEY['\"]\s*\)\s*\}\}")
_BASIC_AUTH_KEYS = ("url_username", "url_password", "force_basic_auth")
_DEPLOYMENT_URL_RE = re.compile(r"/deployments/")
_INJECTED_DEPLOYMENT_URL_RE = re.compile(r"/deployments/\{\{\s*deployment_name\s*\}\}(?:[/?#]|$)")
_POD_EXEC_URL_RE = re.compile(r"/pods/.+/exec(?:\?|$)")

_ANSIBLE_DATETIME_ISO_RE = re.compile(r"\{\{\s*ansible_date_time\.iso8601\s*\}\}")
_NOW_UTC_EXPR = "{{ now(utc=True, fmt='%Y-%m-%dT%H:%M:%SZ') }}"
_LOCAL_BEARER = "Bearer {{ lookup('env', 'K8S_AUTH_API_KEY') }}"
_PROXY_BEARER = "Bearer {{ token_acm }}"
_KUBERNETES_PATH_RE = re.compile(r"/apis?/")
_VARIABLE_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_HUB_BASE_RE = re.compile(r"^\{\{\s*hub_url\s*\}\}(?:/|$)")
_ROLLOUT_STATUS_FIELDS = "observedGeneration|updatedReplicas|readyReplicas|availableReplicas"
_INVALID_GENERATION_RE = re.compile(r"\.json\.(?:(?:status|spec)\.)?generation\b")


def fix_ansible_facts(yaml_text: str) -> str:
    """Replace gather_facts-dependent expressions with fact-free equivalents."""
    return _ANSIBLE_DATETIME_ISO_RE.sub(_NOW_UTC_EXPR, yaml_text)


def quote_jinja(yaml_text: str) -> str:
    """Quote bare Jinja2 expressions so the YAML is valid for Ansible."""

    def _replacer(m):
        prefix, value = m.group(1), m.group(2)
        if value.startswith('"') or value.startswith("'"):
            return m.group(0)
        return f'{prefix}"{value}"'

    return _UNQUOTED_JINJA_RE.sub(_replacer, yaml_text)


def _walk_tasks(task_list):
    """Yield tasks, recursing into block/rescue/always."""
    for task in task_list or []:
        if not isinstance(task, dict):
            continue
        yield task
        for block_key in _BLOCK_KEYS:
            sub_tasks = task.get(block_key)
            if isinstance(sub_tasks, list):
                yield from _walk_tasks(sub_tasks)


def _iter_tasks(play: dict):
    """Yield tasks from each section of a play."""
    for section_key in _TASK_LIST_KEYS:
        yield from _walk_tasks(play.get(section_key))


def _iter_uri_tasks(play: dict):
    """Yield (task_dict, uri_dict) for every uri task in a play."""
    for task in _iter_tasks(play):
        uri = task.get("uri") or task.get("ansible.builtin.uri")
        if isinstance(uri, dict):
            yield task, uri


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

    is_hub_url = bool(_HUB_BASE_RE.match(str(url)))

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


def _resolved_uri_url(play: dict, uri: dict) -> str:
    """Expand simple play variable aliases without evaluating generated Jinja."""
    url = str(uri.get("url", ""))
    play_vars = play.get("vars") or {}
    if not isinstance(play_vars, dict):
        return url
    for _ in range(len(play_vars)):
        expanded = _VARIABLE_RE.sub(lambda match: str(play_vars.get(match[1], match[0])), url)
        if expanded == url:
            break
        url = expanded
    return url


def _expected_bearer(play: dict, uri: dict) -> str:
    url = _resolved_uri_url(play, uri)
    if DEPLOYMENT_MODE == "hub-spoke" and _HUB_BASE_RE.match(url):
        return _PROXY_BEARER
    local_host = _K8S_HOST_LOOKUP_RE.match(url)
    if DEPLOYMENT_MODE == "single-cluster" and local_host and url[local_host.end() :].startswith("/"):
        return _LOCAL_BEARER
    return ""


def _fix_kubernetes_auth(parsed):
    for play in _iter_plays(parsed):
        for _task, uri in _iter_uri_tasks(play):
            bearer = _expected_bearer(play, uri)
            headers = uri.get("headers")
            if not bearer or (headers is not None and not isinstance(headers, dict)):
                continue
            headers = {name: value for name, value in (headers or {}).items() if str(name).lower() != "authorization"}
            headers["Authorization"] = bearer
            uri["headers"] = headers
            for auth_key in _BASIC_AUTH_KEYS:
                uri.pop(auth_key, None)
    return parsed


def _authentication_validation_errors(play: dict):
    for _task, uri in _iter_uri_tasks(play):
        if not (_expected_bearer(play, uri) or _KUBERNETES_PATH_RE.search(str(uri.get("url", "")))):
            continue
        headers = uri.get("headers")
        authorization = (
            [value for name, value in headers.items() if str(name).lower() == "authorization"]
            if isinstance(headers, dict)
            else []
        )
        if (
            len(authorization) != 1
            or not isinstance(authorization[0], str)
            or not re.fullmatch(r"Bearer\s+\S.*", authorization[0])
        ):
            yield (
                "Missing or invalid Kubernetes Authorization header. Use a headers mapping with "
                "Authorization: Bearer and the injected credential: K8S_AUTH_API_KEY in "
                "single-cluster mode, token_acm in hub-spoke mode."
            )


def _iter_deployment_polls(play: dict):
    for task, uri in _iter_uri_tasks(play):
        if str(uri.get("method", "GET")).upper() != "GET":
            continue
        if _DEPLOYMENT_URL_RE.search(_resolved_uri_url(play, uri)) and "until" in task:
            yield task


def _fix_rollout_condition(condition: str, register: str) -> str:
    """Repair the observed job's typo and tolerate status fields absent during rollout."""
    prefix = rf"(?<![\w.]){re.escape(register)}\.json\."
    condition = re.sub(prefix + r"generation\b", f"{register}.json.status.observedGeneration", condition)
    status_field = re.compile(
        prefix + rf"status\.(?:{_ROLLOUT_STATUS_FIELDS})"
        r"(?![\w.]|\s*\|\s*default\s*\(|\s+is\s+(?:not\s+)?defined\b)"
    )
    return status_field.sub(lambda match: f"({match[0]} | default(0))", condition)


def _fix_deployment_polls(parsed):
    for play in _iter_plays(parsed):
        for task in _iter_deployment_polls(play):
            register = task.get("register")
            if not isinstance(register, str) or not re.fullmatch(r"[A-Za-z_]\w*", register):
                continue
            conditions = task["until"]
            if isinstance(conditions, str):
                task["until"] = _fix_rollout_condition(conditions, register)
            elif isinstance(conditions, list) and all(isinstance(condition, str) for condition in conditions):
                task["until"] = [_fix_rollout_condition(condition, register) for condition in conditions]
    return parsed


def _rollout_validation_errors(play: dict):
    for task in _iter_deployment_polls(play):
        conditions = task["until"] if isinstance(task["until"], list) else [task["until"]]
        if any(isinstance(condition, str) and _INVALID_GENERATION_RE.search(condition) for condition in conditions):
            yield (
                "Invalid Deployment rollout generation check. Use the GET task's registered result: "
                "json.status.observedGeneration >= json.metadata.generation, and compare "
                "status.updatedReplicas, readyReplicas, and availableReplicas with spec.replicas. "
                "Default missing status counts to 0; do not use json.generation or status.generation."
            )


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
            if "status" in uri:
                uri.setdefault("status_code", uri.pop("status"))
            if str(uri.get("method", "")).upper() == "PATCH":
                _fix_patch_uri(uri)
    return parsed


def _fix_template_strings(value):
    """Rewrite expressions after parsing so embedded quotes cannot corrupt YAML."""
    if isinstance(value, str):
        return fix_ansible_facts(_PREFIXED_K8S_HOST_RE.sub(r"\1", value))
    if isinstance(value, list):
        return [_fix_template_strings(entry) for entry in value]
    if isinstance(value, dict):
        return {key: _fix_template_strings(entry) for key, entry in value.items()}
    return value


def _is_restart_patch_uri(uri: dict) -> bool:
    """True for the Deployment restart PATCH (the corrective action to preserve)."""
    return (
        str(uri.get("method", "GET")).upper() == "PATCH"
        and "/deployments/" in str(uri.get("url", ""))
        and "kubectl.kubernetes.io/restartedAt" in str(uri.get("body", ""))
    )


def _is_unexecutable_uri(uri: dict, has_restart: bool) -> bool:
    """True for a uri task the cluster cannot run: pods/exec, or a named-pod
    operation after a Deployment restart (the pod is replaced under a new name)."""
    url = str(uri.get("url", ""))
    if _POD_EXEC_URL_RE.search(url):
        return True
    return has_restart and "/pods/" in url


def _prune_task_list(tasks: list, state: dict) -> list:
    """Drop unexecutable uri tasks, tracking whether a restart has happened yet."""
    kept = []
    for task in tasks:
        uri = task.get("uri") or task.get("ansible.builtin.uri") if isinstance(task, dict) else None
        if isinstance(uri, dict):
            if _is_unexecutable_uri(uri, state["has_restart"]):
                logger.warning(f"Pruned unexecutable task from generated playbook: {str(uri.get('url', ''))[:120]}")
                continue
            if _is_restart_patch_uri(uri):
                state["has_restart"] = True
        kept.append(task)
    return kept


def _sections_have_patch(sections: dict) -> bool:
    for tasks in sections.values():
        for task in tasks:
            uri = task.get("uri") or task.get("ansible.builtin.uri") if isinstance(task, dict) else None
            if isinstance(uri, dict) and str(uri.get("method", "GET")).upper() == "PATCH":
                return True
    return False


def _prune_unexecutable_tasks(parsed: list | dict | None) -> list | dict | None:
    """Remove pods/exec and post-restart named-pod tasks a stubborn model keeps
    emitting, while keeping the real fix. A play is left untouched (so the
    validator still rejects it) if pruning would leave no PATCH corrective action."""
    for play in _iter_plays(parsed):
        state = {"has_restart": False}
        pruned = {}
        removed = False
        for section_key in _TASK_LIST_KEYS:
            tasks = play.get(section_key)
            if not isinstance(tasks, list):
                continue
            new_tasks = _prune_task_list(tasks, state)
            removed = removed or len(new_tasks) != len(tasks)
            pruned[section_key] = new_tasks
        if removed and _sections_have_patch(pruned):
            for section_key, new_tasks in pruned.items():
                play[section_key] = new_tasks
    return parsed


def sanitize_playbook(parsed: list | dict | None) -> list | dict | None:
    """Rewrite ALS YAML: cluster-proxy auth, dangerous headers, then PATCH fixes."""
    # A single play may arrive as a bare dict; wrap it so the play iterators run.
    if isinstance(parsed, dict):
        parsed = [parsed]
    parsed = _fix_patch_tasks(parsed)
    parsed = _fix_template_strings(parsed)
    if DEPLOYMENT_MODE == "hub-spoke":
        parsed = _fix_cluster_proxy_auth(parsed)
    parsed = _fix_kubernetes_auth(parsed)
    parsed = _fix_deployment_polls(parsed)
    parsed = _strip_dangerous_headers(parsed)
    parsed = _prune_unexecutable_tasks(parsed)
    return parsed


def _uri_validation_errors(play: dict):
    """Check request bodies and reads of pods invalidated by a restart."""
    has_restart = False
    for _task, uri in _iter_uri_tasks(play):
        method = str(uri.get("method", "GET")).upper()
        url = str(uri.get("url", ""))
        body = uri.get("body")
        if _POD_EXEC_URL_RE.search(url):
            yield (
                "Remove pods/exec calls: uri cannot perform Kubernetes exec streaming, and the "
                "remediation credential does not grant pods/exec. Restart via the Deployment "
                "and verify its rollout; do not run nslookup or systemctl in a pod."
            )
        if method == "PATCH" and not isinstance(body, (dict, list)):
            yield (
                "Invalid PATCH body: use a YAML mapping (or a list for JSON Patch), "
                "not a JSON string or scalar. Check for extra or missing braces."
            )
        if method == "PATCH" and "/deployments/" in url and "kubectl.kubernetes.io/restartedAt" in str(body):
            has_restart = True
        if has_restart and "/pods/" in url:
            yield (
                "Do not poll a named pod after restarting its Deployment: that pod can be deleted. "
                "Remove the old-pod polling task and any subsequent GET/assertion or other operation on that pod; "
                "verify Deployment rollout instead, including status.observedGeneration and "
                "updated, ready, and available replica counts."
            )


def _target_validation_errors(play: dict):
    """Require the Deployment target supplied by the execution pipeline."""
    for task in _iter_tasks(play):
        facts = task.get("set_fact") or task.get("ansible.builtin.set_fact")
        if isinstance(facts, dict) and "deployment_name" in facts:
            yield (
                "Remove set_fact assigning deployment_name and its discovery tasks. "
                "AAP already supplies deployment_name; use that injected value directly."
            )
    for _task, uri in _iter_uri_tasks(play):
        url = str(uri.get("url", ""))
        if "/deployments/" in url and not _INJECTED_DEPLOYMENT_URL_RE.search(url):
            yield (
                "Use /deployments/{{ deployment_name }} in every Deployment URL. "
                "Remove Deployment discovery: do not derive its name from pod_name, "
                "ownerReferences, or a regex, and do not hardcode the workload name."
            )


def playbook_validation_error(parsed: list | dict | None) -> str:
    """Reject invalid request bodies and stale pod checks before publishing."""
    if not isinstance(parsed, list):
        return "Invalid YAML; return a YAML list of Ansible plays with balanced quotes."
    errors = (
        error
        for play in _iter_plays(parsed)
        for validate in (
            _uri_validation_errors,
            _target_validation_errors,
            _authentication_validation_errors,
            _rollout_validation_errors,
        )
        for error in validate(play)
    )
    return "\n".join(dict.fromkeys(errors))
