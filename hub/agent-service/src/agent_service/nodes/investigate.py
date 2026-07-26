import asyncio
import os

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from loguru import logger

from agent_service.models import GraphConfig
from agent_service.utils import invoke_tool

_LLAMASTACK_HOST = os.environ.get("LLAMASTACK_HOST", "localhost")
_LLAMASTACK_PORT = os.environ.get("LLAMASTACK_PORT", "8321")
_GRANITE_MODEL = os.environ.get("GRANITE_MODEL_NAME", "granite-4.0-8b")

_llm = ChatOpenAI(
    base_url=f"http://{_LLAMASTACK_HOST}:{_LLAMASTACK_PORT}/v1",
    model=_GRANITE_MODEL,
    api_key="unused",
)

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
                    "tenant": {"type": "string", "description": "Tenant identifier"},
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
                    "labels": {"type": "string", "description": "Label selector"},
                    "text": {"type": "string", "description": "Text to search for"},
                    "tenant": {"type": "string", "description": "Tenant identifier"},
                    "duration": {"type": "string", "description": "Time window, e.g. '1h'", "default": "1h"},
                    "limit": {"type": "integer", "description": "Max results", "default": 50},
                },
                "required": ["namespace"],
            },
        },
    },
]

_SYSTEM_PROMPT = """\
You are a Kubernetes incident investigator. Your job is to gather evidence about \
an incident by calling available tools. You are NOT analyzing or deciding — just collecting facts.

Available tools:
- get_events(namespace, limit): Get recent Kubernetes events for a namespace
- find_error_patterns(namespace, app, duration, top_n, tenant, regex): Find recurring error patterns in logs
- get_pod_logs(pod_name, namespace, container, tail_lines): Get logs from a specific pod
- search_logs(namespace, pod, container, labels, text, tenant, duration, limit): Search aggregated logs via Loki

You may call multiple tools in a single response when it would be efficient. \
If one tool fails, consider using an alternative (e.g., search_logs via Loki \
when get_pod_logs times out).

Given the log event and any enriched pod status, decide which tools to call to \
gather evidence. Stop when you have enough context or cannot gather more useful information.

Do NOT analyze root causes or recommend fixes — just gather raw evidence."""


def _extract_events(result: dict) -> list[dict]:
    items = result.get("items", result.get("events", []))
    if isinstance(items, list):
        return items
    return [result]


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
        log_event = state.log_event
        cluster_events = list(state.cluster_events)
        recent_errors = list(state.recent_errors)
        pod_logs = state.pod_logs
        log_search_results = list(state.log_search_results)

        pod_status_summary = ""
        if state.pod_status:
            pod_status_summary = f"\nEnriched pod status: {state.pod_status}"

        user_content = (
            f"Log event: {log_event.raw}"
            f"{pod_status_summary}"
        )

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        try:
            async with asyncio.timeout(config.investigate_timeout):
                for _ in range(config.investigate_max_iterations):
                    response = await _llm.ainvoke(messages, tools=_TOOLS)

                    if not response.tool_calls:
                        break

                    messages.append(response)

                    tasks = [
                        (tc, asyncio.create_task(_call_tool(tc["name"], tc["args"])))
                        for tc in response.tool_calls
                    ]

                    for tool_call, task in tasks:
                        tool_result = await task
                        tool_name = tool_call["name"]

                        if "error" not in tool_result:
                            if tool_name == "get_events":
                                cluster_events.extend(_extract_events(tool_result))
                            elif tool_name == "find_error_patterns":
                                patterns = tool_result.get("patterns", [])
                                recent_errors.extend(patterns)
                            elif tool_name == "get_pod_logs":
                                logs = tool_result.get("logs", "")
                                if logs:
                                    pod_logs = logs if not pod_logs else pod_logs + "\n" + logs
                            elif tool_name == "search_logs":
                                results = tool_result.get("results", [])
                                log_search_results.extend(results)

                        messages.append(
                            ToolMessage(
                                content=str(tool_result),
                                tool_call_id=tool_call["id"],
                            )
                        )
        except TimeoutError:
            logger.warning("Investigate node timed out, returning partial evidence")
        except Exception:
            logger.opt(exception=True).warning("Investigate node failed, returning partial evidence")

        return {
            "cluster_events": cluster_events,
            "recent_errors": recent_errors,
            "pod_logs": pod_logs,
            "log_search_results": log_search_results,
        }

    return investigate_node
