# Configure incident response integrations

Follow the [README](../README.md#deploy) to clone the repository and create `.env` before running repository commands. Complete the prerequisites below and configure `.env`, then return to the README deployment steps.

## AAP setup

Install AAP with its controller enabled, and create a Write-scope token as described in the [README requirements](../README.md#minimum-software-requirements). Set `AAP_TOKEN` and `AAP_NAMESPACE` in `.env`. The token's user needs permission to create and update projects and credentials, create/copy/update job templates, use the selected inventory and credentials, and execute jobs. ACM authentication also creates a custom credential type, which requires an AAP administrator.

### Create the Network project and base template

1. In AAP, open **Automation Execution → Projects → Create project**. Use these values, then save and wait for a successful project sync:

   | Field | Value |
   | --- | --- |
   | Name | `ai-driven-network-remediation` |
   | Organization | Your automation organization |
   | Source control type | `Git` |
   | Source control URL | `https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git` |
   | Source control branch/tag/commit | `main`, or the revision you deploy |
   | Options | Enable **Update revision on launch** |

   See [AAP project configuration](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/using_automation_execution/controller-projects) for UI details.

2. Open **Automation Execution → Templates → Create template → Create job template**. Set:

   | Field | Value |
   | --- | --- |
   | Name | `lightspeed-runner` |
   | Job type | `Run` |
   | Inventory | Your inventory for remediation jobs |
   | Project | `ai-driven-network-remediation` |
   | Playbook | `hub/helm/charts/network/files/playbooks/restart-nginx.yaml` as the initial selection |
   | Variables | Enable **Prompt on launch** |
   | Execution environment | An environment containing the Ansible modules required by your playbooks |

   Save the template. The initial playbook satisfies AAP's required field; generated templates select their own playbook. See [AAP job templates](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/using_automation_execution/controller-job-templates).

3. The bootstrap uses organization ID `1`. If you selected another organization, add `--set network.aapCredential.organizationId=YOUR_ORGANIZATION_ID` to the existing `HELM_EXTRA_ARGS` assignment in `.env`, replacing the placeholder with its numeric ID.

Deployment creates the `lightspeed-generated` project against the bundled Gitea repository. It uploads the Network playbooks from `hub/helm/charts/network/files/playbooks/` and creates matching templates by copying `lightspeed-runner`, including its inventory and credentials. Generated playbooks use the same Gitea project. AAP must be able to reach the internal Gitea service; external Gitea is not fully supported by the current bootstrap.

### Prepare RAN automation

Create working playbooks and the matching `ran-*` job templates from the [RAN remediation guide](telco-oran-remediation.md#root-cause--template-mapping-decide-node), including the fallback `ran-generic-remediation`. Configure each template's inventory, execution environment, target-system credentials, and **Prompt on launch** for variables. Deployment does not install these templates or attach their credentials on a real controller.

## Lightspeed

The Network workflow uses the Ansible Lightspeed intelligent assistant to generate remediation playbooks. Follow the [Lightspeed installation procedure](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/installing_on_openshift_container_platform/deploying-chatbot-operator) for the chatbot's model configuration secret and enable `spec.lightspeed.disabled: false` in the AAP resource. Set `spec.lightspeed.chatbot_config_secret_name` to that secret's name.

The linked AAP 2.5 guide now marks that intelligent assistant version as deprecated and directs users to AAP 2.6 or later for supported functionality. This repository has no verified Lightspeed compatibility matrix; use the installation documentation matching your AAP release.

Set these values in `.env`:

```bash
ENABLE_LIGHTSPEED=true
LIGHTSPEED_VERIFY_SSL=true
```

Leave `LIGHTSPEED_URL` and `LIGHTSPEED_TOKEN` unset to use discovery. Make selects the first Service whose name contains `lightspeed-chatbot-api`, constructs its internal HTTPS URL, and reads the `api_key` entry from `aap-lightspeed-chatbot-api-key` in `AAP_NAMESPACE`. Your logged-in user must be able to list those Services and read that Secret.

If there are multiple installations, the Secret has another name, or the internal certificate is not trusted by the agent, set `LIGHTSPEED_URL` to the intended API endpoint with a certificate trusted by the agent and `LIGHTSPEED_TOKEN` to its API key. Discovery does not establish certificate trust. This key is separate from `AAP_TOKEN`, which authorizes controller operations. The deployment prerequisite check still requires a `lightspeed-chatbot-api` Service on the cluster when you provide an explicit endpoint. For Lightspeed outside namespace `aap`, also add `--set-string network.agentService.lightspeed.namespace=YOUR_LIGHTSPEED_NAMESPACE` to `HELM_EXTRA_ARGS` so the chart's network policy targets the correct namespace.

For example, use the chatbot API Route supplied by your AAP administrator, with a certificate chain trusted by the agent, and replace these placeholders in `.env`. Use the API endpoint, rather than the AAP console URL:

```bash
LIGHTSPEED_URL=https://your-lightspeed-api.apps.example.com
LIGHTSPEED_TOKEN=replace-with-your-chatbot-api-key
```

## Cluster credentials

The AAP API token authorizes controller operations. Playbooks also need credentials for the cluster or equipment being repaired. The Network bootstrap attaches cluster credentials to `lightspeed-runner`; copied templates inherit them.

### Single cluster

With real AAP, deployment automatically creates the `aap-remediation` ServiceAccount and registers its token as the AAP credential `hub-remediation`. Its Kubernetes API address defaults to `https://kubernetes.default.svc`, so the AAP execution environment must run on or reach that cluster.

The current chart grants cluster-wide `get`, `list`, and `patch` access to all resources in the core, apps, batch, and networking API groups. It stores a ServiceAccount token in a Secret and creates the AAP credential with certificate verification disabled. The original README and chart identify this permission profile as development-only; the quickstart does not provide a hardened production credential profile. Assess these existing permissions before connecting operational workloads.

### ACM hub proxy

For AAP jobs targeting managed clusters, follow the hub identity and addon setup in [Red Hat's multicluster authentication guide](https://developers.redhat.com/articles/2025/09/08/multicluster-authentication-ansible-automation-platform). Ensure ManagedServiceAccount and cluster-proxy addons are enabled and the hub identity has proxy access. The chart expects the identity `aap-integration-serviceaccount` in namespace `aap`.

Find the proxy endpoint and obtain a token for that identity:

```bash
oc get route cluster-proxy-addon-user -n multicluster-engine -o jsonpath='https://{.spec.host}{"\n"}'
oc create token aap-integration-serviceaccount -n aap
```

Put the returned URL and token in `.env`:

```bash
ENABLE_MULTICLUSTER=true
CLUSTER_PROXY_URL=https://your-cluster-proxy.example.com
RHACM_HUB_TOKEN=replace-with-the-hub-service-account-token
```

Renew `RHACM_HUB_TOKEN` before its issued lifetime ends and redeploy to update the AAP credential. The bootstrap creates the `RHACM Cluster Proxy` credential type and `rhacm-hub` credential, which supplies `hub_url` and `token_acm` to playbooks. The incident's `edge_site_id` selects the managed-cluster path under the proxy URL.

`ENABLE_MULTICLUSTER` configures AAP authentication independently of `CLUSTER_COUNT`. In hub-spoke topology, the chart grants the expected hub identity the `edit` role on each configured spoke. If using another identity, add both `network.aapCredential.multicluster.hubServiceAccountNamespace` and `network.aapCredential.multicluster.hubServiceAccountName` overrides to `HELM_EXTRA_ARGS`; the token must belong to that identity. Other topology modes do not create these spoke bindings. See the [multi-cluster deployment guide](multi-cluster-deploy.md) for topology prerequisites.

## Integration endpoints

Use the real-integration `AAP_URL` example in [.env.example](../.env.example) and replace the endpoint before loading `.env`.

| Integration | Configuration behavior |
| --- | --- |
| Network AAP | Make sets `AAP_URL` to the namespace-local mock when enabled, or `http://aap.aap.svc` otherwise. |
| RAN AAP | Uses the same Make `AAP_URL` value as Network. |
| AAP namespace | `AAP_NAMESPACE` controls the prerequisite check and Lightspeed token lookup; it does not change controller URLs. The prerequisite check requires a controller Service named `aap` in that namespace. |

`AAP_URL` is a Make variable shared by both services. Its default is the namespace-local mock when `ENABLE_AAP_MOCK=true` and `http://aap.aap.svc` otherwise. Set `AAP_URL` on the Make command line or in the environment to override both URLs. Direct Helm installs retain the chart defaults. The AAP controller API prefix defaults to `/api/controller/v2`.

## ServiceNow

Network-only, Telco-only, and combined hubs each deploy one `mcp-noc-servicenow` endpoint and register `mcp::noc-servicenow` once with LlamaStack. The Network agent uses that toolgroup for escalation. The Network chatbot uses the configured backend and credentials to read incident counts.

### Use the mock

Make and direct Helm use the namespace-local `http://servicenow-mock:8080` backend by default. Set `ENABLE_SERVICENOW_MOCK=true` in Make or `global.servicenow.mock.enabled=true` in Helm to enable the mock. Clear any previous backend URL override to use the namespace-local default. With your usual model settings loaded, install a Telco-only hub in a custom namespace with this command.

```bash
make helm-install NAMESPACE=hub-demo ENABLE_NETWORK_REMEDIATION=false \
  ENABLE_TELCO_ORAN=true ENABLE_SERVICENOW_MOCK=true
```

Run `make telco-integration-tests NAMESPACE=hub-demo ENABLE_SERVICENOW_MOCK=true` to include the smoke test that discovers the toolgroup and reads an incident created in that hub's mock.

### Connect a real instance

Set `ENABLE_SERVICENOW_MOCK=false` and `SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com` in `.env`, then load it and run `make helm-install` with your profile and deployment settings. Direct Helm takes the same URL as `global.servicenow.url`. The Network agent uses this URL for incident links by default. Keep `network.agentService.servicenowInstanceUrl` if your users open tickets at a separate browser URL.

Choose one credential configuration. The Helm keys in the table go under `global.servicenow.credentials`.

| Authentication | Make | Helm |
| --- | --- | --- |
| API key | `SERVICENOW_API_KEY=<api-key>` | `apiKey: <api-key>` |
| Basic auth | `SERVICENOW_USERNAME=<user>` and `SERVICENOW_PASSWORD=<password>` | `username: <user>` and `password: <password>` |
| Existing Secret | `SERVICENOW_SECRET_NAME=servicenow-credentials` | `existingSecretName: servicenow-credentials` |

API key and Basic auth values create the Helm-managed `noc-servicenow-secret`. An existing Secret must be in the hub namespace and contain `SERVICENOW_API_KEY` or both `SERVICENOW_USERNAME` and `SERVICENOW_PASSWORD`. The API key takes precedence when the Secret contains both forms. For API key provisioning, see the [ServiceNow bootstrap guide](../scripts/servicenow-bootstrap/README.md).

For direct Helm, add the following to your deployment values and pass the file with `-f` to `helm upgrade --install`. This example uses an existing Secret.

```yaml
global:
  servicenow:
    mock:
      enabled: false
    url: https://your-instance.service-now.com
    credentials:
      existingSecretName: servicenow-credentials
```

Set `global.servicenow.mcp.image.repository` and `global.servicenow.mcp.image.tag` to use a custom MCP image. The mock image uses `global.servicenow.mock.image.repository` and `global.servicenow.mock.image.tag`.

### Upgrade an existing Network release

With Make, run `make helm-install` using the same release name and namespace and pass your existing configuration, including any overrides in `HELM_EXTRA_ARGS`. With direct Helm, use the same release and namespace and load the current chart defaults together with the stored release overrides.

```bash
make helm-depend
helm upgrade hub hub/helm --namespace <namespace> --reset-then-reuse-values --wait
```

The upgrade retains the MCP Deployment, Service, credential Secret name, and LlamaStack toolgroup. Move each legacy override to its shared path when you migrate your configuration. Shared fields take precedence, and omitted shared fields can still inherit their legacy values.

| Legacy Helm value | Shared Helm value |
| --- | --- |
| `network.servicenowMock.enabled` | `global.servicenow.mock.enabled` |
| `network.servicenowMock.image` | `global.servicenow.mock.image` |
| `network.mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL` | `global.servicenow.url` |
| `network.chatbotService.env.servicenowUrl` | `global.servicenow.url` |
| `network.mcpSecrets.servicenow` | `global.servicenow.credentials` |
| `network.mcp-servers.mcp-servers.noc-servicenow.image` | `global.servicenow.mcp.image` |

To switch from a legacy API key to Basic auth, set both shared `username` and `password`. They clear the inherited API key unless you also set a shared `apiKey`. For an externally managed `noc-servicenow-secret`, keep `network.mcpSecrets.servicenow.create=false` or set `global.servicenow.credentials.existingSecretName` to that Secret. To switch from an inherited existing Secret to Helm-managed credentials, set `global.servicenow.credentials.existingSecretName` to an empty string and `global.servicenow.credentials.create=true` together with the new credentials.

Helm replaces pods that use the managed credentials when they change. After rotating an externally managed Secret, restart `deployment/mcp-noc-servicenow` in the hub namespace so it reloads the Secret. Also restart `deployment/hub-chatbot-service` and `deployment/servicenow-mock` if they are installed.

## Operational inputs

Ready pods confirm deployment health. To validate incident response, connect the sources your team operates:

- **Network:** Single-cluster deployment leaves log forwarding disabled. Configure the OpenShift Logging operator, Kafka client certificates, `KAFKA_EXTERNAL_HOST`, and `CLUSTER_LOG_FORWARDER_ENABLED=true` using the [single-cluster topology instructions](multi-cluster-deploy.md#scenario-a-single-cluster-cluster_count1).
- **RAN:** Supply a measurement producer for the `ran-combined-metrics` Kafka topic. Each message contains a `kpi_window` of 128 timesteps × 18 KPI channels with `incident_id`, `zone`, and `application` metadata. Follow the [detector guide](../hub/ran-anomaly-detector/README.md) and [detection workflow](telco-oran-anomaly-detection.md). Deployment does not connect radio equipment automatically.

For an incident from your monitored environment, follow its diagnosis in the dashboard, confirm any launched AAP job, and verify the workload or radio condition recovered. Confirm escalated Network incidents in ServiceNow.

## Troubleshooting

Load the deployment's `.env` before using namespace variables. If a diagnosis or remediation does not appear, inspect the relevant service:

```bash
set -a && source .env && set +a
oc logs deployment/hub-agent-service -n "$NAMESPACE" --tail=100
oc logs deployment/hub-ran-anomaly-detector -n "$NAMESPACE" --tail=100
oc logs deployment/hub-ran-rca-service -n "$NAMESPACE" --tail=100
oc logs deployment/hub-ran-remediation-service -n "$NAMESPACE" --tail=100
```

| Symptom | Check |
| --- | --- |
| No Network incidents | Log-forwarding configuration and connectivity to the hub Kafka endpoint. |
| No RAN diagnosis | Measurement format, predictor endpoint ending in `/v1/detect`, and model endpoint credentials. |
| Lightspeed discovery fails | Installed chatbot Service, `AAP_NAMESPACE`, API-key Secret, and permissions to read them. |
| Lightspeed certificate error | The configured endpoint's certificate chain and hostname are trusted by the agent. |
| AAP authorization error | `AAP_TOKEN` validity and the user's project, template, inventory, and credential permissions. |
| AAP bootstrap cannot create templates | `lightspeed-runner` exists, its project synced, the organization ID matches, and AAP can reach Gitea. |
| Playbook cannot reach a target | Template credentials, target API access rules, ACM identity/token, and the incident's managed-cluster name. |
| ServiceNow counts are unavailable | The current dashboard credential limitation described under [Integration endpoints](#integration-endpoints); inspect escalation in ServiceNow directly. |
