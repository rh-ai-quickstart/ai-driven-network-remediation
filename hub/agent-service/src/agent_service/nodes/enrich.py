from loguru import logger

from agent_service.utils import invoke_tool


async def enrich_node(state: dict) -> dict:
    log_event = state.log_event
    try:
        pod_status = await invoke_tool(
            "get_pods",
            {"namespace": log_event.namespace, "edge_site_id": log_event.edge_site_id},
        )
        if pod_status.get("error"):
            logger.warning(f"enrich_node: get_pods soft-failure: {pod_status['error']}")
            pod_status = {}
    except Exception:
        logger.opt(exception=True).warning("enrich_node: get_pods call failed")
        pod_status = {}
    return {"pod_status": pod_status}
