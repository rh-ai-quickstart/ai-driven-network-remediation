import asyncio

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from agent_service.config import (
    INVESTIGATE_OPENSHIFT_TOOLS,
    INVESTIGATE_TOOL_SCHEMAS,
    get_investigate_system_prompt,
    get_investigate_tools,
    get_llm,
)
from agent_service.models import GraphConfig
from agent_service.utils import EDGE_SITE_STAMP, invoke_tool


def _extract_events(result: dict) -> list[dict]:
    items = result.get("items", result.get("events", []))
    if isinstance(items, list):
        return items
    return [result]


def _default_tool_args(tool_name: str, tool_args: dict, log_event) -> dict:
    # Only the OpenShift tools route by cluster via edge_site_id. The Loki tools
    # select a log stream via a separate tenant argument (application|
    # infrastructure|audit), so we leave it at the server default instead.
    schema = INVESTIGATE_TOOL_SCHEMAS.get(tool_name, {})
    allowed = schema.get("properties", {})
    args = {k: v for k, v in tool_args.items() if k in allowed}
    for key, prop in allowed.items():
        if prop.get("type") == "integer" and isinstance(args.get(key), float):
            args[key] = int(args[key])
    if tool_name in INVESTIGATE_OPENSHIFT_TOOLS and "edge_site_id" not in args:
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


def _stamp_pod_logs(logs: str, edge_site_id: str, pod_name: str) -> str:
    source = f"{EDGE_SITE_STAMP} {edge_site_id}" if edge_site_id else ""
    pod = f"Pod: {pod_name}" if pod_name else ""
    provenance = " ".join(part for part in (source, pod) if part)
    if not provenance:
        return logs
    return "\n".join(f"{provenance} | {line}" for line in logs.splitlines())


def _merge_tool_result(
    tool_name: str,
    tool_result: dict,
    evidence: dict,
    edge_site_id: str = "",
    pod_name: str = "",
) -> None:
    if tool_result.get("error"):
        return
    if tool_name == "get_events":
        evidence["cluster_events"].extend(_extract_events(tool_result))
    elif tool_name == "find_error_patterns":
        evidence["recent_errors"].extend(tool_result.get("patterns", []))
    elif tool_name == "get_pod_logs":
        logs = tool_result.get("logs", "")
        if logs:
            logs = _stamp_pod_logs(logs, edge_site_id, pod_name)
            prev = evidence["pod_logs"]
            evidence["pod_logs"] = logs if not prev else prev + "\n" + logs
    elif tool_name == "search_logs":
        evidence["log_search_results"].extend(tool_result.get("logs", []))
    elif tool_name == "get_pod_spec":
        formatted = _format_pod_resources(tool_result)
        if edge_site_id:
            formatted = f"{EDGE_SITE_STAMP} {edge_site_id}\n{formatted}"
        prev = evidence["resource_specs"]
        evidence["resource_specs"] = formatted if not prev else prev + "\n" + formatted


def make_investigate_node(config: GraphConfig):
    tools = get_investigate_tools()
    tool_names = {tool["function"]["name"] for tool in tools}
    system_prompt = get_investigate_system_prompt(tools)

    async def _call_tool(tool_name: str, tool_args: dict) -> dict:
        if tool_name not in tool_names:
            return {"error": f"Tool {tool_name} is not enabled"}
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
        if not tools:
            logger.info("No investigation tools enabled, returning existing evidence")
            return evidence

        pod_status_summary = f"\nEnriched pod status: {state.pod_status}" if state.pod_status else ""
        messages = [
            SystemMessage(content=system_prompt),
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
                    response = await get_llm().ainvoke(messages, tools=tools, tool_choice=tool_choice)
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

                    for tool_call, args, tool_result in zip(tool_calls, defaulted_args, results):
                        _merge_tool_result(
                            tool_call["name"],
                            tool_result,
                            evidence,
                            args.get("edge_site_id", ""),
                            args.get("pod_name", ""),
                        )
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
