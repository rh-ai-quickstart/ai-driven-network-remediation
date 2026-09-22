"""ran-remediation-service CLI entry — runs one anomaly through the pipeline locally."""

from __future__ import annotations


def main() -> None:
    """Local dev entry point: runs a sample anomaly through the graph without Kafka."""
    import asyncio

    from ran_remediation_service.graph import build_graph

    sample = {
        "incident_id":     "demo-001",
        "zone":            "A",
        "application":     "File",
        "ad_label":        "anomalous",
        "ad_confidence":   0.9995,
        "root_cause":      "Signal degradation consistent with antenna misalignment.",
        "recommended_fix": "Adjust antenna tilt per vendor guide Section 4.3.2.",
    }

    graph = build_graph()
    result = asyncio.run(graph.ainvoke(sample))
    print(f"template={result.get('template_name')} success={result.get('success')} job_id={result.get('job_id')}")
