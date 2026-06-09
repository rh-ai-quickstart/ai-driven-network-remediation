import pytest
from mcp_kafka.validators import (
    clamp,
    suggest_topics,
)


class TestSuggestTopics:
    TOPICS = ["system-alerts", "noc-alerts", "remediation-jobs", "agent-events", "incident-audit"]

    def test_prefix_match(self):
        result = suggest_topics("noc", self.TOPICS)
        assert "noc-alerts" in result

    def test_fuzzy_match_typo(self):
        result = suggest_topics("system-alert", self.TOPICS)
        assert "system-alerts" in result

    def test_no_match(self):
        result = suggest_topics("zzzzz-unknown", self.TOPICS)
        assert result == []

    def test_dedup_prefix_and_fuzzy(self):
        result = suggest_topics("system-alerts", self.TOPICS)
        assert result.count("system-alerts") == 1

    def test_max_five_results(self):
        many_topics = [f"topic-{i}" for i in range(20)]
        result = suggest_topics("topic", many_topics)
        assert len(result) <= 5

    def test_exact_match_returned(self):
        result = suggest_topics("noc-alerts", self.TOPICS)
        assert "noc-alerts" in result


class TestClamp:
    @pytest.mark.parametrize(
        "value, lo, hi, expected_clamped, expected_original",
        [
            (20, 1, 100, 20, None),  # within range — no clamping
            (100, 1, 100, 100, None),  # upper boundary — no clamping
            (500, 1, 100, 100, 500),  # above max — clamped, original preserved
            (1, 1, 100, 1, None),  # lower boundary — no clamping
            (0, 1, 100, 1, None),  # below min — clamped to 1
            (-5, 1, 100, 1, None),  # negative — clamped to 1
            (5000, 100, 15000, 5000, None),  # timeout within range
            (15000, 100, 15000, 15000, None),  # timeout upper boundary
            (30000, 100, 15000, 15000, 30000),  # timeout above max
            (50, 100, 15000, 100, None),  # timeout below min
        ],
    )
    def test_clamping(self, value, lo, hi, expected_clamped, expected_original):
        clamped, original = clamp(value, lo, hi)
        assert clamped == expected_clamped
        assert original == expected_original
