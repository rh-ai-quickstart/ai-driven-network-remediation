from loguru import logger


def context_node(state: dict) -> dict:
    logger.info("Context node invoked")
    return {"context_snippets": ["placeholder-context-snippet"]}
