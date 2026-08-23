import asyncio

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from agent_service.config import INVESTIGATE_SYSTEM_PROMPT, get_llm
from agent_service.models import GraphConfig
from agent_service.utils import invoke_tool

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "Get Kubernetes events for a namespace",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "limit": {"type": "integer", "description": "Max events to return", "default": 20},
                    "edge_site_id": {"type": "string", "description": "Edge site / cluster ID (e.g. edge-site-01). Defaults to the incident cluster."},
                },
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_error_patterns",
            "description": "Find recurring error patterns in logs for an application",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "app": {"type": "string", "description": "Application name"},
                    "duration": {"type": "string", "description": "Time window, e.g. '1h'", "default": "1h"},
                    "top_n": {"type": "integer", "description": "Max patterns to return", "default": 10},
                    "regex": {"type": "string", "description": "Optional regex filter"},
                },
                "required": ["namespace", "app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": "Get logs from a specific pod",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Pod name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "container": {"type": "string", "description": "Container name"},
                    "tail_lines": {"type": "integer", "description": "Number of lines from the end", "default": 100},
                    "edge_site_id": {"type": "string", "description": "Edge site / cluster ID (e.g. edge-site-01). Defaults to the incident cluster."},
                },
                "required": ["pod_name", "namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "Search aggregated logs via Loki",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "pod": {"type": "string", "description": "Pod name filter"},
                    "container": {"type": "string", "description": "Container name filter"},
                    "labels": {
                        "type": "object",
                        "description": "Label selector",
                        "additionalProperties": {"type": "string"},
                    },
                    "text": {"type": "string", "description": "Text to search for"},
                    "duration": {"type": "string", "description": "Time window, e.g. '1h'", "default": "1h"},
                    "limit": {"type": "integer", "description": "Max results", "default": 50},
                },
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_spec",
            "description": (
                "Get structured spec for a pod as JSON. Includes container "
                "resource limits, requests, probes, and env vars. Supports "
                "prefix matching (e.g. 'myapp' finds 'myapp-6b7f8c-x2k9z')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Pod name (exact or prefix)"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "edge_site_id": {"type": "string", "description": "Edge site / cluster ID (e.g. edge-site-01). Defaults to the incident cluster."},
                },
                "required": ["name", "namespace"],
            },
        },
    },
]


_TOOL_DOCS = "\n".join(
    f"- {t['function']['name']}"
    f"({', '.join(t['function']['parameters']['properties'])}): "
    f"{t['function']['description']}"
    for t in _TOOLS
)
_SYSTEM_PROMPT = f"{INVESTIGATE_SYSTEM_PROMPT}\n\nAvailable tools:\n{_TOOL_DOCS}"


def _extract_events(result: dict) -> list[dict]:
    items = result.get("items", result.get("events", []))
    if isinstance(items, list):
        return items
    return [result]


_OPENSHIFT_TOOLS = {"get_events", "get_pod_logs", "get_pod_spec"}


def _default_tool_args(tool_name: str, tool_args: dict, log_event) -> dict:
    # Only the OpenShift tools route by cluster via edge_site_id. The Loki tools
    # select a log stream via a separate tenant argument (application|
    # infrastructure|audit), so we leave it at the server default instead.
    args = dict(tool_args)
    if tool_name in _OPENSHIFT_TOOLS and "edge_site_id" not in args:
        args["edge_site_id"] = log_event.edge_site_id
    return args


def _format_pod_resources(tool_result: dict) -> str:
    """Format a pod's container limits/requests into readable text for evidence."""
    name = tool_result.get("name", "unknown")
    spec = tool_result.get("spec", {})
    lines = [f"Pod: {name}"]
    for container in spec.get("containers", []):
        lines.append(f"  Container: {container.get('name', '?')}")
        resources = container.get("resources", {})
        for section in ("limits", "requests"):
            vals = resources.get(section, {})
            if vals:
                parts = ", ".join(f"{k}: {v}" for k, v in vals.items())
                lines.append(f"    {section}: {parts}")
    return "\n".join(lines)


def _merge_tool_result(tool_name: str, tool_result: dict, evidence: dict) -> None:
    if tool_result.get("error"):
        return
    if tool_name == "get_events":
        evidence["cluster_events"].extend(_extract_events(tool_result))
    elif tool_name == "find_error_patterns":
        evidence["recent_errors"].extend(tool_result.get("patterns", []))
    elif tool_name == "get_pod_logs":
        logs = tool_result.get("logs", "")
        if logs:
            prev = evidence["pod_logs"]
            evidence["pod_logs"] = logs if not prev else prev + "\n" + logs
    elif tool_name == "search_logs":
        evidence["log_search_results"].extend(tool_result.get("logs", []))
    elif tool_name == "get_pod_spec":
        formatted = _format_pod_resources(tool_result)
        prev = evidence["resource_specs"]
        evidence["resource_specs"] = formatted if not prev else prev + "\n" + formatted


def make_investigate_node(config: GraphConfig):
    async def _call_tool(tool_name: str, tool_args: dict) -> dict:
        try:
            async with asyncio.timeout(config.tool_call_timeout):
                return await invoke_tool(tool_name, tool_args)
        except TimeoutError:
            logger.warning(f"Tool call {tool_name} timed out after {config.tool_call_timeout}s")
            return {"error": f"{tool_name} timed out after {config.tool_call_timeout}s"}
        except Exception as exc:
            logger.warning(f"Tool call {tool_name} failed: {exc}")
            return {"error": str(exc)}

    async def investigate_node(state) -> dict:
        logger.info("Investigate node invoked")

        evidence = {
            "cluster_events": list(state.cluster_events),
            "recent_errors": list(state.recent_errors),
            "pod_logs": state.pod_logs,
            "resource_specs": state.resource_specs,
            "log_search_results": list(state.log_search_results),
        }

        pod_status_summary = f"\nEnriched pod status: {state.pod_status}" if state.pod_status else ""
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=f"Log event: {state.log_event.raw}{pod_status_summary}"),
        ]

        try:
            async with asyncio.timeout(config.investigate_timeout):
                for iteration in range(config.investigate_max_iterations):
                    tool_choice = "required" if iteration == 0 else "auto"
                    logger.info(
                        f"Investigate iteration {iteration + 1}/{config.investigate_max_iterations}"
                        f" tool_choice={tool_choice}"
                    )
                    response = await get_llm().ainvoke(messages, tools=_TOOLS, tool_choice=tool_choice)
                    if not response.tool_calls:
                        logger.info(f"Agent stopped calling tools after {iteration + 1} iteration(s)")
                        break

                    messages.append(response)
                    tool_calls = response.tool_calls
                    defaulted_args = [_default_tool_args(tc["name"], tc["args"], state.log_event) for tc in tool_calls]
                    logger.debug(
                        f"Agent requested {len(tool_calls)} tool(s): "
                        f"{list(zip((tc['name'] for tc in tool_calls), defaulted_args))}"
                    )

                    results = await asyncio.gather(
                        *[_call_tool(tc["name"], args) for tc, args in zip(tool_calls, defaulted_args)]
                    )

                    for tool_call, tool_result in zip(tool_calls, results):
                        _merge_tool_result(tool_call["name"], tool_result, evidence)
                        messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))

                logger.info(
                    f"Investigate finished: events={len(evidence['cluster_events'])}"
                    f" errors={len(evidence['recent_errors'])}"
                    f" log_results={len(evidence['log_search_results'])}"
                    f" has_pod_logs={bool(evidence['pod_logs'])}"
                )
        except TimeoutError:
            logger.warning("Investigate node timed out, returning partial evidence")
        except Exception:
            logger.opt(exception=True).warning("Investigate node failed, returning partial evidence")

        return evidence

    return investigate_node
