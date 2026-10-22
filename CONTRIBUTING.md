# Develop and contribute

Use this guide to develop components and exercise workflows with simulated integrations. For installation with real services, start with the [main README](README.md). See the [architecture guide](docs/architecture.md) for component relationships.

## Development environment

Cluster development needs the platform, storage, and model prerequisites in the [README](README.md#requirements). Mocks replace AAP and ServiceNow only: diagnosis still needs a working language-model endpoint, and RAN detection still needs a predictor accepting `POST /v1/detect`. The [predictor guide](model-serving/ran-ml-service/README.md) covers that endpoint.

For Python development and tests, install Python 3.13 and `uv`. Image builds use Podman by default and require registry access. Run the following commands from the repository root in Bash, while logged in to a dedicated development cluster:

```bash
test -e .env || cp .env.example .env
```

Edit `.env` before loading it:

- Set `ADNR_LLM_ID`, `ADNR_LLM_URL`, and `ADNR_LLM_TOKEN` to your real model configuration.
- Set `ADNR_DETECT_INFERENCE_URL` and, when required, `ADNR_DETECT_TOKEN` for RAN. To work on Network alone, set `ENABLE_TELCO_ORAN=false` instead.
- Set `ENABLE_AAP_MOCK=true` and `ENABLE_SERVICENOW_MOCK=true` for development. Leave `AAP_SECRET_NAME` unset so Make can create the mock credentials. Remove real integration URL overrides from `HELM_EXTRA_ARGS` and `GITEA_URL` when reusing a configuration.
- Keep `CLUSTER_COUNT=1` for a single development cluster. Use dedicated `NAMESPACE` and `EDGE_NAMESPACE` values; teardown deletes both namespaces and their data.

Load the development configuration and deploy:

```bash
set -a && source .env && set +a
make deploy ENABLE_AAP_MOCK=true ENABLE_SERVICENOW_MOCK=true
```

Make uses the exported values; reload `.env` after edits or opening a new shell. Removing an assignment does not clear a value already exported in your shell; use `unset VARIABLE` or a fresh shell. Command-line Make assignments override exported values. Service-local variables are not automatically forwarded to deployed pods.

## Use the development mocks

**Mocks are for development and testing only.** The Makefile defaults both mock flags to `true`; choose them explicitly when following this guide. The main README configures real integrations for users.

| Setting | Development behavior |
| --- | --- |
| `ENABLE_AAP_MOCK=true` | Deploys `aap-mock`, directs Network and RAN automation requests to it, and provides simulated job results. The mock also supplies the generated-playbook repository API used by Network. |
| `ENABLE_SERVICENOW_MOCK=true` | Deploys `servicenow-mock` and directs the Network ticketing tools to its simulated incident API. |
| `ENABLE_LIGHTSPEED=false` | Leaves optional playbook generation disabled. Existing-template AAP requests still use the selected real or mock controller. |
| `LIGHTSPEED_SKIP_AAP=true` | Skips execution of newly generated Lightspeed playbooks in the Network agent only; see the next section for how to apply it. |

`ENABLE_AAP_MOCK=false` selects real AAP; it does **not** disable automation. Use the mock when developing other components without an AAP controller. Likewise, `ENABLE_SERVICENOW_MOCK=false` requires real ServiceNow configuration. Follow the main README when switching to real integrations.

These settings are wired by the Makefile, including service URLs and credentials. Enabling a chart flag directly with Helm does not reproduce all of Make's connection settings.

Check the mock deployments and open the dashboards:

```bash
oc rollout status deployment/aap-mock -n "$NAMESPACE"
oc rollout status deployment/servicenow-mock -n "$NAMESPACE"
oc get route hub-frontend -n "$NAMESPACE" -o jsonpath='https://{.spec.host}{"\n"}'
oc get route hub-ran-frontend -n "$NAMESPACE" -o jsonpath='https://{.spec.host}{"\n"}'
```

Skip the ServiceNow and Network dashboard checks when Network is disabled, and skip the RAN dashboard check when RAN is disabled. These resource names assume the default Helm release name, `hub`.

- In Network, select **Trigger CrashLoop Demo** and follow the incident. This publishes a synthetic alert; it does not crash a workload.
- In RAN, select **Antenna Failure** under **Demo Mode** and follow detection, diagnosis, and remediation. This sends a public lab trace through the real inference pipeline.

Mock job success confirms simulated automation, not an actual repair. Mock ServiceNow incidents are stored in memory and are lost on restart. The AAP mock also holds its job records in memory. Mocks do not make the entire deployment inert: cluster access remains real, and the [edge fast-path healer](docs/edge-fast-path-healer.md) can modify workloads independently of AAP.

To exercise a RAN remediation failure, patch the selected mock job template with
`mock_outcome` set to `launch_error`, `timeout`, or `failed`. The setting applies
to subsequent launches of that template until changed or the mock restarts.
`successful` restores the normal mock behavior. For example, after forwarding
the mock service to `localhost:8080`:

```bash
curl -X PATCH http://localhost:8080/api/controller/v2/job_templates/9/ \
  -H 'Content-Type: application/json' -d '{"mock_outcome":"failed"}'
# Trigger the Antenna Failure demo and inspect its incident card.
curl -X PATCH http://localhost:8080/api/controller/v2/job_templates/9/ \
  -H 'Content-Type: application/json' -d '{"mock_outcome":"successful"}'
```

Template 9 is the seeded `ran-cell-recovery` template. A missing template or
launch error leaves the chosen template on the incident without a job ID;
timeout and failed-job outcomes include the launched job ID.

If diagnosis does not complete, inspect the relevant logs and check the model endpoints:

```bash
oc logs deployment/hub-agent-service -n "$NAMESPACE" --tail=100
oc logs deployment/hub-ran-anomaly-detector -n "$NAMESPACE" --tail=100
oc logs deployment/hub-ran-rca-service -n "$NAMESPACE" --tail=100
```

## Develop Lightspeed without executing generated playbooks

`LIGHTSPEED_SKIP_AAP=true` is a development-only setting for inspecting generated playbooks while working on other components. It skips the Network agent's AAP execution step after successful Lightspeed generation. Generation still needs a real Lightspeed service and a language model; a successful result can still be stored in the reference-document store.

This setting is **not a global automation switch**: existing-template Network remediation, RAN automation, and the edge healer are unaffected. Keep the AAP mock enabled if those workflows must use simulated AAP responses.

For a locally started Network agent, export `LIGHTSPEED_SKIP_AAP=true` in that process's environment. Adding it to the root `.env` alone does not configure cluster pods: the current Makefile and Helm template do not forward it. For an existing development deployment, apply the temporary override explicitly:

```bash
oc set env deployment/hub-agent-service -n "$NAMESPACE" LIGHTSPEED_SKIP_AAP=true
oc rollout status deployment/hub-agent-service -n "$NAMESPACE"
```

This restarts the agent pods. Configure `ENABLE_LIGHTSPEED=true` and a reachable Lightspeed URL/token when testing generation; the [Lightspeed walkthrough](docs/LIGHTSPEED-DEMO-SCRIPT.md) describes that workflow. Treat the direct deployment override as temporary and check it after subsequent Helm updates.

Restore normal generated-playbook execution when finished:

```bash
oc set env deployment/hub-agent-service -n "$NAMESPACE" LIGHTSPEED_SKIP_AAP-
oc rollout status deployment/hub-agent-service -n "$NAMESPACE"
```

For a local process, unset `LIGHTSPEED_SKIP_AAP` and restart it.

## Build and test changes

Run focused tests for the component you changed. For example, these commands run the Network agent tests in a subshell so your current directory stays at the repository root:

```bash
(cd hub/agent-service && uv sync --group dev && uv run pytest)
```

For the repository's unit-test target, first generate the RAN fixtures. Fixture generation downloads the TelecomTS dataset from Hugging Face and requires network access; those generated files are not committed.

```bash
make generate-fixtures
make unit-tests
```

Integration tests require deployed services and can create incidents and exercise automation. Run them against your development deployment with the mock configuration loaded. Install the test dependencies, then choose the target matching your deployed components:

```bash
(cd hub/integration-tests && uv sync)
make network-integration-tests
# For RAN instead: make telco-integration-tests
# For both components: make integration-tests
```

The Make targets open temporary local port-forwards. RAN integration tests also need to reach the configured predictor from your shell.

To build changed application images, set `REGISTRY` to a registry namespace you can push to and `VERSION` to your development tag. Then run:

```bash
make build-all-images
make push-all-images
```

These targets cover the selected application components. They exclude the mock images; build and push those separately before deploying a custom registry/tag with mocks enabled:

```bash
make build-push-aap-mock
make build-push-servicenow-mock
make deploy ENABLE_AAP_MOCK=true ENABLE_SERVICENOW_MOCK=true
```

Keep the same registry, version, component selections, and topology configuration across build, push, and deployment. Report which checks you ran and any failures or skipped checks when submitting a change.

## Remove a development deployment

Load the same configuration used for installation and follow the [README cleanup procedure](README.md#delete). Use the [multi-cluster guide](docs/multi-cluster-deploy.md#scenario-c-teardown) for deployments with spokes. Teardown removes stored development incidents and documents along with the application namespaces.
