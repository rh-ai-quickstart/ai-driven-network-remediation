import httpx
import respx
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
        result = search_logs()
        assert result["success"] is False
        assert "At least one filter" in result["error"]

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
        assert 'container="ng\\"inx"' in result["query"]

    @respx.mock
    def test_label_value_quote_escaped(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = search_logs(namespace="default", labels={"env": 'pr"od'})
        assert 'env="pr\\"od"' in result["query"]

    def test_label_key_with_quotes_rejected(self):
        result = search_logs(namespace="default", labels={'bad"key': "val"})
        assert result["success"] is False
        assert "Invalid label key" in result["error"]

    def test_label_key_with_equals_rejected(self):
        result = search_logs(namespace="default", labels={"bad=key": "val"})
        assert result["success"] is False
        assert "Invalid label key" in result["error"]

    def test_invalid_tenant(self):
        result = search_logs(namespace="test", tenant="bad")
        assert result["success"] is False
        assert "Invalid tenant" in result["error"]

    def test_invalid_duration(self):
        result = search_logs(namespace="test", duration="999d")
        assert result["success"] is False

    @respx.mock
    def test_http_error(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(400, text="bad query")
        )
        result = search_logs(namespace="test")
        assert result["success"] is False
        assert "400" in result["error"]

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
        assert 'container="nginx"' in result["query"]

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
        result = search_logs_regex()
        assert result["success"] is False
        assert "At least one filter" in result["error"]


class TestQueryLogql:
    @respx.mock
    def test_raw_logql(self):
        respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_STREAMS_RESPONSE)
        )
        result = query_logql(logql_query='{namespace="test"} |= "error"')
        assert result["query"] == '{namespace="test"} |= "error"'

    def test_empty_query(self):
        result = query_logql(logql_query="")
        assert result["success"] is False

    def test_invalid_query_no_selector(self):
        result = query_logql(logql_query='namespace="test"')
        assert result["success"] is False
        assert "stream selector" in result["error"]


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
        result = query_metrics(metric_type="bad")
        assert result["success"] is False
        assert "Invalid metric_type" in result["error"]

    def test_top_errors_removed(self):
        result = query_metrics(metric_type="top_errors_by_count")
        assert result["success"] is False
        assert "Invalid metric_type" in result["error"]

    def test_step_larger_than_duration(self):
        result = query_metrics(metric_type="error_rate", step="2h", duration="1h")
        assert result["success"] is False
        assert "larger than duration" in result["error"]

    @respx.mock
    def test_with_app_filter(self):
        route = respx.get(f"{BASE}/application/loki/api/v1/query_range").mock(
            return_value=httpx.Response(200, json=SAMPLE_MATRIX_RESPONSE)
        )
        result = query_metrics(metric_type="log_volume", namespace="dark-noc-edge", app="nginx")
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

    def test_missing_namespace(self):
        result = find_error_patterns(namespace="")
        assert result["success"] is False
        assert "namespace is required" in result["error"]

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
        result = find_error_patterns(namespace="dark-noc-edge")
        assert result["success"] is False
