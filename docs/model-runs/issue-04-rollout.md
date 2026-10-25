# RCA incident rollout

The classifier artifact, deployment commands, and parity evidence are documented in
[the classifier rollout](issue-196-rca-rollout.md). This note covers the hub
integration and live incident probe after the classifier refresh.

## Full incident probe after refresh

Keep `ADNR_DETECT_INFERENCE_URL` and `ADNR_CLASSIFY_INFERENCE_URL` configured
in the hub release. Port-forward the chatbot, RCA, and remediation Services,
then run the dedicated integration test with:

```sh
RCA_REAL_E2E=1 RCA_FIXTURE_SCENARIO=co_channel_interference_severe \
RCA_EXPECTED_MODE=used \
RCA_EXPECTED_CLASSIFY_URL="$ADNR_CLASSIFY_INFERENCE_URL" \
RCA_EXPECTED_CLASSIFY_TOKEN="${ADNR_CLASSIFY_TOKEN:-}" \
RCA_DEPLOYMENT_NAMESPACE=<hub-namespace> \
RCA_EVIDENCE_FILE=/tmp/issue-04-incident.json \
RAN_RCA_SERVICE_URL=http://localhost:<rca-port> \
RAN_REMEDIATION_SERVICE_URL=http://localhost:<remediation-port> \
RAN_CHATBOT_SERVICE_URL=http://localhost:<chatbot-port> \
uv run --directory hub/integration-tests pytest tests/telco/test_rca_real_e2e.py -q
```

The probe triggers the existing fixture through AD and anomaly Kafka. It
asserts incident identity, the full KPI window, pinned ML advice, enriched
Kafka and API records, the Mantis-derived operational category, the existing
remediation template handoff, and chat. Its JSON evidence file records the resulting
incident ID and chat reply. Record the two InferenceService revisions, Route
host, deployed consumer URL, and probe evidence when the live rollout runs.

## Incident execution status

For the incident probe, the available `ci-hub-telco-oran` release had older
0.1.5 RCA/chatbot images and no classifier URL. The current RCA and chatbot
images were built and staged in that namespace's internal registry, then only
those two Deployments were updated to immutable image digests. The RCA
Deployment was configured with the same-cluster classifier URL and an empty token
before `rca-196-refresh-2`; its URL was unchanged afterward.
Both Deployments were Ready. This targeted CI update was outside the saved Helm
release values. The nightly workflow now builds the RCA, chatbot, and shared
predictor images from its checked-out commit, deploys the classifier, and
passes the commit-tagged consumer images and same-cluster classifier URL to
Helm. `hub/helm/values-ci-rca.yaml` retains the URL for subsequent nightly
upgrades. An empty classifier URL leaves RCA unready; the nightly release sets
the URL explicitly.

The post-refresh `co_channel_interference_severe` fixture produced incident
`9334e533`. The [live evidence](issue-04-incident.json) records the ML advice:
Co-Channel Interference (Severe), index 2, confidence 0.9502, status `used`.
The integration probe checked the original incident identity and complete KPI
window across AD, anomaly Kafka, enriched Kafka, and the API; it also checked
rejection of malformed Kafka messages. The Mantis class mapped to operational
category `interference`; RAG/Granite supplied the explanation and fix.
Remediation selected `ran-interference-mitigation`, and chat distinguished the
Mantis prediction and category from the LLM explanation. The live probe passed
on 2026-10-15. Current RCA
and chatbot consumer unit suites passed locally with 107 and 70 tests.
