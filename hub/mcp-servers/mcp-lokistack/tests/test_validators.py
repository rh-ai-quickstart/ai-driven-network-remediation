import pytest
from mcp_lokistack.validators import (
    validate_duration,
    validate_limit,
    validate_logql,
    validate_metric_type,
    validate_namespace,
    validate_step,
    validate_tenant,
)


class TestValidateTenant:
    @pytest.mark.parametrize("tenant", ["application", "infrastructure", "audit"])
    def test_valid(self, tenant):
        validate_tenant(tenant)

    @pytest.mark.parametrize("tenant", ["", "admin", "AUDIT", "app"])
    def test_invalid(self, tenant):
        with pytest.raises(ValueError, match="Invalid tenant"):
            validate_tenant(tenant)


class TestValidateDuration:
    @pytest.mark.parametrize("duration", ["30s", "5m", "1h", "6h", "24h", "1d"])
    def test_valid(self, duration):
        validate_duration(duration)

    @pytest.mark.parametrize("duration", ["", "abc", "5x", "h1", "1.5h", "-1h"])
    def test_invalid_format(self, duration):
        with pytest.raises(ValueError, match="Invalid duration"):
            validate_duration(duration)

    def test_exceeds_max(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_duration("48h")

    def test_zero_duration(self):
        with pytest.raises(ValueError, match="greater than zero"):
            validate_duration("0s")

    def test_exactly_at_max(self):
        validate_duration("24h")


class TestValidateLimit:
    def test_valid(self):
        assert validate_limit(50) == 50

    def test_clamps_to_ceiling(self):
        assert validate_limit(1000) == 500

    def test_minimum(self):
        assert validate_limit(1) == 1

    def test_rejects_zero(self):
        with pytest.raises(ValueError, match="limit must be >= 1"):
            validate_limit(0)

    def test_rejects_negative(self):
        with pytest.raises(ValueError, match="limit must be >= 1"):
            validate_limit(-5)


class TestValidateNamespace:
    @pytest.mark.parametrize(
        "ns",
        [
            "default",
            "kube-system",
            "dark-noc-edge",
            "my-ns-123",
            "a",
            "a1",
        ],
    )
    def test_valid(self, ns):
        validate_namespace(ns)

    @pytest.mark.parametrize(
        "ns",
        [
            "",
            "-invalid",
            "invalid-",
            "UPPER",
            "has_underscore",
            "has.dot",
            "a" * 64,
        ],
    )
    def test_invalid(self, ns):
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace(ns)


class TestValidateLogql:
    def test_valid(self):
        validate_logql('{namespace="test"} |= "error"')

    def test_minimal(self):
        validate_logql("{}")

    def test_empty(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_logql("")

    def test_whitespace_only(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_logql("   ")

    def test_too_long(self):
        with pytest.raises(ValueError, match="too long"):
            validate_logql("{" + "a" * 2048 + "}")

    def test_no_selector(self):
        with pytest.raises(ValueError, match="stream selector"):
            validate_logql('namespace="test"')

    def test_reversed_braces(self):
        with pytest.raises(ValueError, match="closing brace.*before"):
            validate_logql("} bad {")

    def test_no_leading_selector(self):
        with pytest.raises(ValueError, match="must start with"):
            validate_logql("random text {something}")

    def test_aggregation_prefix_accepted(self):
        validate_logql('sum by (ns) (count_over_time({namespace="test"} [5m]))')

    def test_rate_prefix_accepted(self):
        validate_logql('rate({namespace="test"} [5m])')

    def test_semicolon_allowed(self):
        validate_logql('{namespace="test"} |= "a;b"')

    def test_double_dash_allowed(self):
        validate_logql('{namespace="test"} |= "a--b"')


class TestValidateMetricType:
    @pytest.mark.parametrize("mt", ["error_rate", "log_volume"])
    def test_valid(self, mt):
        validate_metric_type(mt)

    @pytest.mark.parametrize("mt", ["", "errors", "rate", "count", "top_errors_by_count"])
    def test_invalid(self, mt):
        with pytest.raises(ValueError, match="Invalid metric_type"):
            validate_metric_type(mt)


class TestValidateStep:
    def test_valid(self):
        validate_step("5m", "1h")

    def test_equal(self):
        validate_step("1h", "1h")

    def test_step_larger_than_duration(self):
        with pytest.raises(ValueError, match="larger than duration"):
            validate_step("2h", "1h")
