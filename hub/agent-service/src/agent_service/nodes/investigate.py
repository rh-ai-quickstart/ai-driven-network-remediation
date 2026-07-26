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
    }
]

_SYSTEM_PROMPT = """\
You are a Kubernetes incident investigator. Your job is to gather evidence about \
an incident by calling available tools. You are NOT analyzing or deciding — just collecting facts.

Available tools:
- get_events(namespace, limit): Get recent Kubernetes events for a namespace

Given the log event and any enriched pod status, decide whether to call a tool to \
gather more evidence. If you have enough context or cannot gather more useful \
information, stop calling tools.

Do NOT analyze root causes or recommend fixes — just gather raw evidence."""


def make_investigate_node(config: GraphConfig):
    async def investigate_node(state) -> dict:
        logger.info("Investigate node invoked")
        log_event = state.log_event
        cluster_events = list(state.cluster_events)

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

                    for tool_call in response.tool_calls:
                        tool_name = tool_call["name"]
                        tool_args = tool_call["args"]

                        try:
                            tool_result = await invoke_tool(tool_name, tool_args)
                        except Exception as exc:
                            tool_result = {"error": str(exc)}
                            logger.warning(f"Tool call {tool_name} failed: {exc}")

                        if tool_name == "get_events" and "error" not in tool_result:
                            items = tool_result.get("items", tool_result.get("events", []))
                            if isinstance(items, list):
                                cluster_events.extend(items)
                            else:
                                cluster_events.append(tool_result)

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

        return {"cluster_events": cluster_events}

    return investigate_node
