from loguru import logger


def request_approval_node(state: dict) -> dict:
    logger.info("Request Approval node invoked")
    return {"decision": "request_approval", "awaiting_human_approval": True}
