from helpers import make_log_event

from agent_service.config import EDGE_NAMESPACE
from agent_service.edge_site import (
    extract_edge_site_id_from_alert,
    resolve_edge_site_id,
)


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
