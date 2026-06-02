import pytest
from mcp_kafka.validators import (
    clamp_max_messages,
    clamp_timeout_ms,
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


class TestClampMaxMessages:
    @pytest.mark.parametrize(
        "value, expected_clamped, expected_original",
        [
            (20, 20, None),  # within range — no clamping
            (100, 100, None),  # upper boundary — no clamping
            (500, 100, 500),  # above max — clamped, original preserved
            (1, 1, None),  # lower boundary — no clamping
            (0, 1, None),  # below min — clamped to 1
            (-5, 1, None),  # negative — clamped to 1
        ],
    )
    def test_clamping(self, value, expected_clamped, expected_original):
        clamped, original = clamp_max_messages(value)
        assert clamped == expected_clamped
        assert original == expected_original


class TestClampTimeoutMs:
    @pytest.mark.parametrize(
        "value, expected_clamped, expected_original",
        [
            (5000, 5000, None),  # within range — no clamping
            (15000, 15000, None),  # upper boundary — no clamping
            (30000, 15000, 30000),  # above max — clamped, original preserved
            (100, 100, None),  # lower boundary — no clamping
            (50, 100, None),  # below min — clamped to 100
        ],
    )
    def test_clamping(self, value, expected_clamped, expected_original):
        clamped, original = clamp_timeout_ms(value)
        assert clamped == expected_clamped
        assert original == expected_original
