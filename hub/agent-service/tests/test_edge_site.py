import json

from agent_service.config import EDGE_NAMESPACE
from agent_service.edge_site import (
    extract_edge_site_id_from_alert,
    remediation_should_retry,
    resolve_edge_site_id,
)
from helpers import make_log_event


class TestExtractEdgeSiteIdFromAlert:
    def test_canonical_labels(self):
        data = {"labels": {"edge_site_id": "edge-02"}}
        assert extract_edge_site_id_from_alert(data) == "edge-02"

    def test_kubernetes_labels_from_clf(self):
        data = {
            "kubernetes": {
                "namespace_name": "dark-noc-edge",
                "labels": {"edge_site_id": "edge-01"},
            }
        }
        assert extract_edge_site_id_from_alert(data) == "edge-01"

    def test_openshift_labels_filter_shape(self):
        data = {"openshift": {"edge_site_id": "edge-01"}}
        assert extract_edge_site_id_from_alert(data) == "edge-01"

    def test_adnr_site_label_on_pod(self):
        data = {
            "kubernetes": {
                "labels": {"adnr.io/site-id": "edge-01"},
            }
        }
        assert extract_edge_site_id_from_alert(data) == "edge-01"


class TestResolveEdgeSiteId:
    def test_defaults_dark_noc_edge_namespace_to_edge_01(self):
        event = make_log_event(namespace=EDGE_NAMESPACE, edge_site_id="unknown")
        assert resolve_edge_site_id(event) == "edge-01"

    def test_resource_specs_stamp(self):
        event = make_log_event(edge_site_id="unknown")
        specs = "Edge site: edge-site-02\nmemory: 64Mi"
        assert resolve_edge_site_id(event, resource_specs=specs) == "edge-site-02"


class TestRemediationShouldRetry:
    def test_no_retry_when_site_unknown(self):
        assert remediation_should_retry("some error", "unknown", 1, 1) is False

    def test_no_retry_on_cluster_proxy_unknown_path(self):
        err = "502 ... cluster-proxy-user.../unknown/apis/..."
        assert remediation_should_retry(err, "edge-01", 1, 1) is False

    def test_retry_when_site_valid_and_error_generic(self):
        assert remediation_should_retry("timeout", "edge-01", 1, 1) is True
