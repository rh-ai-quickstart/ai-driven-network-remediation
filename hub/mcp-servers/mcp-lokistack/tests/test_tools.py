from unittest.mock import patch

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError
from mcp_lokistack.tools import (
    find_error_patterns,
    query_logql,
    query_metrics,
    search_logs,
    search_logs_regex,
)

from .conftest import (
    SAMPLE_MATRIX_RESPONSE,
    SAMPLE_STREAMS_RESPONSE,
)

BASE = "http://localhost:3100/api/logs/v1"


class TestSearchLogs:
    @respx.mock
    def test_structured_query(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="dark-noc-edge")
        assert result["count"] == 3
        assert result["tenant"] == "application"
        assert 'kubernetes_namespace_name="dark-noc-edge"' in result["query"]

    def test_no_filters(self):
        with pytest.raises(ToolError, match="At least one filter"):
            search_logs()

    @respx.mock
    def test_pod_regex_safety(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="default", pod="my.pod(name")
        assert r"my\.pod\(name" in result["query"]

    @respx.mock
    def test_container_quote_escaped(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="default", container='ng"inx')
        assert 'kubernetes_container_name="ng\\"inx"' in result["query"]

    @respx.mock
    def test_label_value_quote_escaped(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="default", labels={"env": 'pr"od'})
        assert 'env="pr\\"od"' in result["query"]

    def test_label_key_with_quotes_rejected(self):
        with pytest.raises(ToolError, match="Invalid label key"):
            search_logs(namespace="default", labels={'bad"key': "val"})

    def test_label_key_with_equals_rejected(self):
        with pytest.raises(ToolError, match="Invalid label key"):
            search_logs(namespace="default", labels={"bad=key": "val"})

    def test_invalid_tenant(self):
        with pytest.raises(ToolError, match="Invalid tenant"):
            search_logs(namespace="test", tenant="bad")

    def test_invalid_duration(self):
        with pytest.raises(ToolError):
            search_logs(namespace="test", duration="999d")

    @respx.mock
    def test_http_error(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(400, text="bad query")
        )
        with pytest.raises(ToolError, match="400"):
            search_logs(namespace="test")

    @respx.mock
    def test_with_text(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="test", text="error")
        assert "(?i)error" in result["query"]

    @respx.mock
    def test_text_is_escaped(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="test", text="foo.bar(baz)")
        assert r"foo\.bar\(baz\)" in result["query"]

    @respx.mock
    def test_container_filter(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="default", container="nginx")
        assert 'kubernetes_container_name="nginx"' in result["query"]

    @respx.mock
    def test_labels_filter(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="default", labels={"env": "prod"})
        assert 'env="prod"' in result["query"]


class TestSearchLogsRegex:
    @respx.mock
    def test_with_regex(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs_regex(namespace="test", regex="timeout|refused")
        assert "timeout|refused" in result["query"]

    def test_no_filters(self):
        with pytest.raises(ToolError, match="At least one filter"):
            search_logs_regex()


class TestQueryLogql:
    @respx.mock
    def test_raw_logql(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = query_logql(logql_query='{kubernetes_namespace_name="test"} |= "error"')
        assert result["query"] == ('{kubernetes_namespace_name="test"} |= "error"')

    def test_empty_query(self):
        with pytest.raises(ToolError):
            query_logql(logql_query="")

    def test_invalid_query_no_selector(self):
        with pytest.raises(ToolError, match="stream selector"):
            query_logql(logql_query='namespace="test"')

    def test_bare_namespace_rejected(self):
        with pytest.raises(ToolError, match="kubernetes_namespace_name"):
            query_logql(logql_query='{namespace="test"} |= "error"')

    @respx.mock
    def test_kubernetes_namespace_name_accepted(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = query_logql(logql_query='{kubernetes_namespace_name="test"}')
        assert result["count"] == 3


class TestQueryMetrics:
    @respx.mock
    def test_error_rate(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        result = query_metrics(metric_type="error_rate", namespace="dark-noc-edge")
        assert result["metric_type"] == "error_rate"
        assert result["total"] == 20
        assert len(result["data_points"]) == 3

    @respx.mock
    def test_error_rate_uses_severity_regex(self):
        route = respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        query_metrics(metric_type="error_rate", namespace="dark-noc-edge")
        request = route.calls[0].request
        query_param = str(request.url.params.get("query", ""))
        assert "error|fatal|critical|panic|exception" in query_param

    @respx.mock
    def test_error_rate_groups_by_namespace(self):
        route = respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        query_metrics(metric_type="error_rate", namespace="dark-noc-edge")
        request = route.calls[0].request
        query_param = str(request.url.params.get("query", ""))
        assert "sum by (kubernetes_namespace_name)" in query_param

    @respx.mock
    def test_log_volume(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        result = query_metrics(metric_type="log_volume", namespace="dark-noc-edge")
        assert result["metric_type"] == "log_volume"

    @respx.mock
    def test_data_points_include_labels(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        result = query_metrics(metric_type="log_volume", namespace="dark-noc-edge")
        assert "labels" in result["data_points"][0]

    def test_invalid_metric_type(self):
        with pytest.raises(ToolError, match="Invalid metric_type"):
            query_metrics(metric_type="bad")

    def test_top_errors_removed(self):
        with pytest.raises(ToolError, match="Invalid metric_type"):
            query_metrics(metric_type="top_errors_by_count")

    def test_step_larger_than_duration(self):
        with pytest.raises(ToolError, match="larger than duration"):
            query_metrics(metric_type="error_rate", step="2h", duration="1h")

    @respx.mock
    def test_with_app_filter(self):
        route = respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        result = query_metrics(
            metric_type="log_volume",
            namespace="dark-noc-edge",
            app="nginx",
        )
        request = route.calls[0].request
        query_param = str(request.url.params.get("query", ""))
        assert 'app="nginx"' in query_param
        assert result["app"] == "nginx"


class TestFindErrorPatterns:
    @respx.mock
    def test_basic(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = find_error_patterns(namespace="dark-noc-edge")
        assert "patterns" in result
        assert result["total_errors"] > 0
        assert result["pattern_count"] > 0

    @respx.mock
    def test_uses_severity_regex(self):
        route = respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        find_error_patterns(namespace="dark-noc-edge")
        request = route.calls[0].request
        query_param = str(request.url.params.get("query", ""))
        assert "error|fatal|critical|panic|exception" in query_param

    @respx.mock
    def test_empty_results(self):
        empty = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(return_value=httpx.Response(200, json=empty))
        result = find_error_patterns(namespace="dark-noc-edge")
        assert result["total_errors"] == 0
        assert result["patterns"] == []

    @respx.mock
    def test_with_app_filter(self):
        route = respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = find_error_patterns(namespace="dark-noc-edge", app="nginx")
        request = route.calls[0].request
        query_param = str(request.url.params.get("query", ""))
        assert 'app="nginx"' in query_param
        assert result["app"] == "nginx"

    @respx.mock
    def test_http_error(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(500, text="server error")
        )
        with pytest.raises(ToolError):
            find_error_patterns(namespace="dark-noc-edge")


class TestFindErrorPatternsValidation:
    def test_invalid_tenant_raises(self):
        with pytest.raises(ToolError, match="Invalid tenant"):
            find_error_patterns(namespace="dark-noc-edge", tenant="xyz")

    @respx.mock
    def test_corrections_passed_through(self):
        respx.get(f"{BASE}/audit/loki/api/v1/query_range").mock(
            return_value=httpx.Response(
                200, json=SAMPLE_STREAMS_RESPONSE
            ),
        )
        result = find_error_patterns(
            namespace="dark-noc-edge", tenant="admin"
        )
        assert result["tenant"] == "audit"
        assert "corrections" in result


class TestEmptyResultHints:
    @respx.mock
    def test_hints_when_empty(self):
        empty = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(return_value=httpx.Response(200, json=empty))
        result = search_logs(namespace="dark-noc-edge")
        assert result["count"] == 0
        assert "hints" in result
        assert any("healthy" in h for h in result["hints"])

    @respx.mock
    def test_no_hints_when_results_found(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="dark-noc-edge")
        assert result["count"] > 0
        assert "hints" not in result

    @respx.mock
    def test_fuzzy_namespace_hint_on_empty(self):
        empty = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(return_value=httpx.Response(200, json=empty))
        with patch(
            "mcp_lokistack.tools_search.get_label_values",
            return_value=["dark-noc-edge", "monitoring", "default"],
        ):
            result = search_logs(namespace="dark-noc-edg")
        assert result["count"] == 0
        assert any("dark-noc-edge" in h for h in result["hints"])

    @respx.mock
    def test_label_values_error_is_swallowed(self):
        empty = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(return_value=httpx.Response(200, json=empty))
        with patch(
            "mcp_lokistack.tools_search.get_label_values",
            side_effect=Exception("network error"),
        ):
            result = search_logs(namespace="dark-noc-edge")
        assert result["count"] == 0
        assert "hints" in result


class TestAutoCorrection:
    @respx.mock
    def test_tenant_auto_corrected(self):
        respx.get(f"{BASE}/audit/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="test", tenant="admin")
        assert result["tenant"] == "audit"
        assert "corrections" in result
        assert any("admin" in c and "audit" in c for c in result["corrections"])

    def test_tenant_no_match_raises(self):
        with pytest.raises(ToolError, match="Invalid tenant"):
            search_logs(namespace="test", tenant="xyz")

    @respx.mock
    def test_happy_path_no_corrections(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="test", tenant="application")
        assert "corrections" not in result

    @respx.mock
    def test_metric_type_auto_corrected(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        result = query_metrics(metric_type="error_rates", namespace="dark-noc-edge")
        assert result["metric_type"] == "error_rate"
        assert "corrections" in result

    @respx.mock
    def test_tenant_auto_corrected_in_metrics(self):
        respx.get(f"{BASE}/audit/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        result = query_metrics(
            metric_type="error_rate",
            namespace="dark-noc-edge",
            tenant="admin",
        )
        assert result["tenant"] == "audit"
        assert "corrections" in result


class TestEnrichEmptyResults:
    def test_unknown_param_skipped(self):
        from mcp_lokistack.tools_search import _enrich_empty_results

        result = {
            "count": 0,
            "hints": ["No logs matched."],
        }
        enriched = _enrich_empty_results(result, "application", unknown_param="value")
        assert len(enriched["hints"]) == 1


class TestSemanticChecks:
    def test_bare_namespace_rejected(self):
        with pytest.raises(ToolError, match="kubernetes_namespace_name"):
            query_logql(logql_query='{namespace="test"} |= "error"')

    def test_namespace_in_comma_rejected(self):
        with pytest.raises(ToolError, match="kubernetes_namespace_name"):
            query_logql(logql_query=('{app="nginx", namespace="test"} |= "error"'))

    def test_bare_pod_rejected(self):
        with pytest.raises(ToolError, match="kubernetes_pod_name"):
            query_logql(logql_query='{pod="my-pod"} |= "error"')

    def test_bare_container_rejected(self):
        with pytest.raises(ToolError, match="kubernetes_container_name"):
            query_logql(logql_query='{container="nginx"} |= "error"')

    @respx.mock
    def test_kubernetes_namespace_name_passes(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = query_logql(logql_query='{kubernetes_namespace_name="test"} |= "error"')
        assert result["count"] == 3

    @respx.mock
    def test_kubernetes_pod_name_passes(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = query_logql(
            logql_query='{kubernetes_pod_name="my-pod"} |= "error"'
        )
        assert result["count"] == 3

    @respx.mock
    def test_bare_label_in_content_filter_not_rejected(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = query_logql(
            logql_query='{kubernetes_namespace_name="test"} |= "{namespace=foo}"'
        )
        assert result["count"] == 3
