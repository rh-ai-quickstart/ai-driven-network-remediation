"""Input validation with AI-friendly error messages."""

import difflib


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


def clamp(value: int, lo: int, hi: int) -> tuple[int, int | None]:
    if value > hi:
        return hi, value
    return max(lo, value), None
