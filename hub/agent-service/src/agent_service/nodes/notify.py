from loguru import logger


def notify_node(state: dict) -> dict:
    logger.info("Notify node invoked")
    return {"notifications_sent": ["placeholder-notification"]}
