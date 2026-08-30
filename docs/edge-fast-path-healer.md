# Edge Fast-Path Healer

Spoke-local remediation for the demo nginx OOM scenario. The edge fast-path healer watches the target Deployment, detects OOMKilled pods or unsafe memory limits, and restarts the workload on the spoke within seconds.

This is a POC-inspired pattern. It is not product Event-Driven Ansible (EDA) and is not an `ansible-rulebook` activation.

## Components

| Component | Role |
|-----------|------|
| **Watcher** (`edge-fast-path-watcher`) | Polls pods and the target Deployment via the Kubernetes API. POSTs structured events to the runner on OOM or unsafe memory limit. |
| **Runner** (`edge-fast-path-runner`) | FastAPI webhook that patches the Deployment (memory bump + rollout restart annotation), enforces cooldown, and emits structured JSON logs for CLF forwarding. |

Cooldown is tracked with the Deployment annotation `adnr.io/fast-path-last-heal`. The watcher ignores OOM `lastState` older than that same cooldown window so a watcher restart does not re-heal a healthy Deployment.

## Helm values

Enable or disable with `fastPathHealer.enabled` in `edge/helm/values.yaml`:

| Value | Default | Purpose |
|-------|---------|---------|
| `fastPathHealer.image.repository` | `quay.io/rh-ai-quickstart/noc-edge-fast-path-healer` | Container image |
| `fastPathHealer.image.tag` | chart `appVersion` | Image tag (`Makefile` `VERSION`, e.g. `0.1.5`) |
| `fastPathHealer.remediation.memoryRequest` | `64Mi` | Patched request after heal |
| `fastPathHealer.remediation.memoryLimit` | `128Mi` | Patched limit after heal |
| `fastPathHealer.remediation.unsafeMemoryLimitMi` | `32` | Trigger heal when limit is at or below this |
| `fastPathHealer.remediation.cooldownSeconds` | `300` | Skip repeat heals within this window |
| `fastPathHealer.watcher.pollIntervalSeconds` | `10` | Watcher poll interval |
| `fastPathHealer.networkPolicy.enabled` | `true` | Restrict watcher → runner traffic |
| `fastPathHealer.resources` | `10m`/`128Mi` request, `100m`/`256Mi` limit | CPU and memory for watcher and runner pods (watcher loads the Kubernetes client; 64Mi caused OOMKilled in practice) |

`siteId` and `nginx.name` are required chart values. They are injected as `EDGE_SITE_ID` and `EDGE_DEPLOYMENT` on both pods.

For a local image that is not yet on Quay, set `fastPathHealer.image.tag` on the ArgoCD Application. Do not commit ad hoc tags into `values.yaml`.

## Lab validation

```bash
# On spoke after ArgoCD sync
oc get deploy -n dark-noc-edge | rg 'edge-nginx|fast-path'
oc logs -n dark-noc-edge deploy/edge-fast-path-watcher --tail=50
oc logs -n dark-noc-edge deploy/edge-fast-path-runner --tail=50

# Trigger OOM demo (hub chatbot or workload stress), then confirm:
# Hub skip reads Deployment metadata. Template annotations are the rollout markers.
oc get deploy edge-nginx -n dark-noc-edge -o jsonpath='{.metadata.annotations}{"\n"}'
oc get deploy edge-nginx -n dark-noc-edge -o jsonpath='{.spec.template.metadata.annotations}{"\n"}'
```

Expect `adnr.io/fast-path-last-heal` and `kubectl.kubernetes.io/restartedAt` on the nginx Deployment after a successful heal. Hub skip uses `adnr.io/fast-path-last-heal` on Deployment metadata.

## Hub agent coordination

The hub agent reads `adnr.io/fast-path-last-heal` on the target Deployment before launching AAP. When the annotation is recent (same 300s cooldown by default), remediate skips the AAP job and records `fast_path_actuation: spoke` in audit output.

## OOM dual-path demo

Step-by-step recording script (in-pod OOM, ArgoCD notes, hub skip proof):
[FAST-PATH-HEALER-DEMO-SCRIPT.md](FAST-PATH-HEALER-DEMO-SCRIPT.md).

Pre-req: `fastPathHealer.enabled=true` on the spoke; hub agent running.

1. OOM the running `edge-nginx` container (`dd` into `/dev/shm`). Do not patch the Deployment if ArgoCD is auto-syncing.
2. Within ~10s: spoke runner logs show `result=success`; deployment has `adnr.io/fast-path-last-heal`.
3. Then publish the hub OOM alert (dashboard or curl) so the agent graph runs.
4. Remediate must NOT launch `scale-up-workers` / `restart-nginx` when fast-path already acted.
5. Slack and `incident-audit` show spoke fast path acted and agent validated (no duplicate `job_id`).

## Known limitations

- Dashboard timeline labels ("Spoke fast path" vs "Hub agent") are follow-up work.
- Hub EDA rulebooks and dedicated Kafka audit events from the healer are out of scope for v1.
