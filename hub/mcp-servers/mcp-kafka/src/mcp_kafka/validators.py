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


def _clamp(value: int, floor: int, cap: int) -> tuple[int, int | None]:
    if value > cap:
        return cap, value
    return max(floor, value), None


def clamp_max_messages(value: int) -> tuple[int, int | None]:
    return _clamp(value, 1, MAX_MESSAGES_CAP)


def clamp_timeout_ms(value: int) -> tuple[int, int | None]:
    return _clamp(value, 100, MAX_TIMEOUT_MS_CAP)
