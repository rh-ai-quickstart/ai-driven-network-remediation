## Parent

rh-ai-quickstart/ai-driven-network-remediation#114

## What to build

Add an Enrich node that calls `get_pods(namespace)` via `invoke_tool` and wire it into the graph between Normalize and RAG Retrieval. The node is a plain async function (no factory pattern — no config dependencies). It makes a single deterministic `invoke_tool("get_pods", {"namespace": log_event.namespace})` call, namespace-scoped, and writes the result to `pod_status`. If the call fails, it writes an empty dict and proceeds without raising.

This slice also adds the new state fields (`cluster_events`, `pod_logs`, `log_search_results`) and GraphConfig fields (`tool_call_timeout`, `investigate_timeout`, `investigate_max_iterations`) needed by the full feature — so downstream slices can build on them immediately.

The graph changes from `normalize → rag_retrieval` to `normalize → enrich → rag_retrieval`. After this slice, `pod_status` flows all the way through the graph to the Audit node.

## Acceptance criteria

- [ ] `IncidentState` has three new fields: `cluster_events: list[dict] = []`, `pod_logs: str = ""`, `log_search_results: list[dict] = []`
- [ ] `GraphConfig` has three new fields: `tool_call_timeout: int = 10`, `investigate_timeout: int = 45`, `investigate_max_iterations: int = 3`
- [ ] `enrich_node` exists as a plain async function, calls `invoke_tool("get_pods", {"namespace": ...})`, and populates `pod_status`
- [ ] On `invoke_tool` failure or timeout, `enrich_node` writes `pod_status = {}` and does not raise
- [ ] Graph wiring: `normalize → enrich → rag_retrieval` replaces `normalize → rag_retrieval`
- [ ] `_patch_graph_nodes` fixture updated to stub Enrich
- [ ] Unit tests for `enrich_node`: successful call, failed/timed-out call
- [ ] Unit tests for new model defaults in `test_models.py`
- [ ] `TestGraphCompilation` extended to verify Enrich node and edges
- [ ] `TestLinearFlow` extended to verify `pod_status` appears in end-to-end state output

## Blocked by

None - can start immediately
