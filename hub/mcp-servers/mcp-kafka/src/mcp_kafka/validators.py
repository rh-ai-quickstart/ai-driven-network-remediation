"""Input validation with AI-friendly error messages."""

import difflib

from .config import MAX_MESSAGES_CAP, MAX_TIMEOUT_MS_CAP


def suggest_topics(topic: str, available_topics: list[str]) -> list[str]:
    prefix_matches = [t for t in available_topics if t.startswith(topic)]
    fuzzy_matches = difflib.get_close_matches(topic, available_topics, n=5, cutoff=0.4)
    seen: set[str] = set()
    result: list[str] = []
    for t in prefix_matches + fuzzy_matches:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result[:5]


def clamp_max_messages(value: int) -> tuple[int, int | None]:
    if value > MAX_MESSAGES_CAP:
        return MAX_MESSAGES_CAP, value
    return max(1, value), None


def clamp_timeout_ms(value: int) -> tuple[int, int | None]:
    if value > MAX_TIMEOUT_MS_CAP:
        return MAX_TIMEOUT_MS_CAP, value
    return max(100, value), None
