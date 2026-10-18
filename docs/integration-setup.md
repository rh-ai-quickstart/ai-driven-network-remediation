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

Use the real-integration `HELM_EXTRA_ARGS` example in [.env.example](../.env.example). Keep all overrides in one assignment and replace endpoint and account placeholders before loading `.env`.

| Integration | Configuration behavior |
| --- | --- |
| Network AAP | Defaults to `http://aap.aap.svc`. Override `network.mcp-servers.mcp-servers.noc-aap.env.AAP_URL` for another address. |
| RAN AAP | Set `telco.ranRemediationService.env.aapUrl` explicitly. |
| AAP namespace | `AAP_NAMESPACE` controls the prerequisite check and Lightspeed token lookup; it does not change controller URLs. The prerequisite check requires a controller Service named `aap` in that namespace. |
| ServiceNow escalation | Set `network.mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL`, `network.mcpSecrets.servicenow.username`, and `network.mcpSecrets.servicenow.password`. |
| ServiceNow dashboard URL | Set `network.chatbotService.env.servicenowUrl` to the instance URL. The current chart does not pass ServiceNow credentials to this backend, so authenticated incident counts are unavailable through the existing deployment configuration. |
| Incident links | `SERVICENOW_INSTANCE_URL` sets the base URL for incident links. |

The AAP controller API prefix defaults to `/api/controller/v2`. A standalone `AAP_URL` assignment is not forwarded by Make. Standalone `SERVICENOW_USERNAME` and `SERVICENOW_PASSWORD` configure local account-provisioning scripts, not the deployed services. See the [ServiceNow setup guide](../scripts/servicenow-bootstrap/README.md) for account provisioning.

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
