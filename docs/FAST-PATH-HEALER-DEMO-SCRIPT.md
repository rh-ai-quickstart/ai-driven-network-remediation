# Fast-Path Healer Demo Script

## Overview

This demo shows the **edge fast-path healer** fixing an nginx OOM on the spoke in seconds,
then the hub agent validating that work and **skipping Ansible**. The healer never calls
AAP. The hub only reads `adnr.io/fast-path-last-heal` on `edge-nginx` after an OOM alert
arrives.

**Flow**: spoke OOM → watcher (~10s) → runner patches Deployment → hub Kafka alert
(dashboard or curl) → agent graph → remediate sees annotation → skip `launch_job` →
Slack + `incident-audit` (`fast_path_actuation: spoke`).

**Total demo duration**: about 2 to 3 minutes once terminals are open.

The dashboard **Trigger OOM Demo** button only publishes a Kafka alert. It does not OOM
the pod. Heal on the spoke first, then click the button.

Operator background: [edge-fast-path-healer.md](edge-fast-path-healer.md).

---

## Pre-Demo Checklist

Replace `hub` with your hub namespace if it differs (`hub-mtalvi` in some labs).
Use the spoke kubeconfig for `dark-noc-edge` commands. The ArgoCD Application for site
`edge-01` is `adnr-edge-edge-site-01`.

| Item | How to verify |
|------|---------------|
| Healer and nginx on spoke | `oc get deploy -n dark-noc-edge \| rg 'edge-nginx\|fast-path'` shows three Deployments Running |
| Hub agent running | `oc get pod -l app.kubernetes.io/component=agent-service -n hub` shows Running |
| Dashboard loads | Hub frontend Route opens (reload once) |
| Slack channel ready (optional) | Bot is a member of the demo channel |
| Cooldown clear | `adnr.io/fast-path-last-heal` on `edge-nginx` is empty or older than 5 minutes |

Cooldown is **300 seconds**. If a heal just ran, wait or jump to [Reset](#reset-for-a-second-run).

---

## Screen Layout

1. **Spoke terminal** (or two panes): watcher logs, runner logs, nginx pod
2. **Hub terminal**: `hub-agent-service` logs
3. **Browser**: dashboard (optional Slack / AAP Controller)

Start tails before you trigger:

```bash
# Spoke
oc logs -f -n dark-noc-edge deploy/edge-fast-path-watcher
oc logs -f -n dark-noc-edge deploy/edge-fast-path-runner

# Hub
oc logs -f -n hub deploy/hub-agent-service
```

---

## Step-by-Step Script

### Step 0. Baseline

On the spoke:

```bash
oc get deploy -n dark-noc-edge | rg 'edge-nginx|fast-path'
oc get pods -n dark-noc-edge

oc get deploy edge-nginx -n dark-noc-edge \
  -o jsonpath='memory={.spec.template.spec.containers[0].resources.limits.memory}{"\n"}heal={.metadata.annotations.adnr\.io/fast-path-last-heal}{"\n"}'
```

Healthy start: limit is `64Mi`, and `heal=` is empty (or older than 5 minutes).

**Say**:
> "Nginx is running on the edge with a tight memory limit. The spoke healer is watching
> it. We will OOM the container locally. The hub will later confirm the spoke already
> acted and will not launch Ansible."

---

### Step 1. Create the issue (prefer this, leave ArgoCD running)

Do **not** patch the Deployment. OOM the running container so GitOps never sees a spec
drift before the heal.

```bash
POD=$(oc get pod -n dark-noc-edge -l app=edge-nginx -o jsonpath='{.items[0].metadata.name}')
echo "oom target: $POD"

oc exec -n dark-noc-edge "$POD" -- \
  sh -c 'dd if=/dev/zero of=/dev/shm/oom bs=1M count=128'
```

Expect the exec to die with **137** (OOMKilled) or a write error, then:

```bash
oc get pod -n dark-noc-edge -l app=edge-nginx \
  -o jsonpath='{range .items[*].status.containerStatuses[*]}{.name} last={.lastState.terminated.reason}{"\n"}{end}'
```

Expect `last=OOMKilled`. Watcher poll is about 10 seconds.

**Say**:
> "The edge nginx container just ran out of memory. The spoke healer should notice
> within about 10 seconds and fix it locally, without waiting for the hub or Ansible."

#### Fallback if exec is blocked: patch the limit

This changes the live Deployment. Auto-sync can restore `64Mi` before the watcher
fires, so pause ArgoCD first (see [Pause ArgoCD](#pause-argocd-patch-fallback-only)).

```bash
oc patch deploy edge-nginx -n dark-noc-edge --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/resources/requests/memory","value":"8Mi"},
  {"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"16Mi"}
]'
```

If `ignoreDifferences` for `edge-nginx` resources and annotations is already on the
Application, selfHeal will not revert the patch and you can skip the pause:

```bash
oc -n openshift-gitops get application.argoproj.io adnr-edge-edge-site-01 \
  -o jsonpath='{.spec.ignoreDifferences}{"\n"}'
```

---

### Step 2. Watch the healer

**Watcher** posts an event, for example a pod OOM with `"pod":"edge-nginx-..."` or an
unsafe-limit event:

```json
{"component":"watcher","posted":true,"status_code":200,"event":{"failure_type":"OOMKilled","deployment":"edge-nginx","reason":"unsafe-memory-limit-16Mi"}}
```

**Runner** then logs:

```json
{"component":"runner","action":"local_fast_path_restart","result":"success","memory_request":"64Mi","memory_limit":"128Mi"}
```

Confirm the patch:

```bash
oc get deploy edge-nginx -n dark-noc-edge -o jsonpath='{.metadata.annotations}{"\n"}'
oc get deploy edge-nginx -n dark-noc-edge -o jsonpath='{.spec.template.metadata.annotations}{"\n"}'
oc get deploy edge-nginx -n dark-noc-edge \
  -o jsonpath='limit={.spec.template.spec.containers[0].resources.limits.memory}{"\n"}'
```

Expect:

- `adnr.io/fast-path-last-heal` (ISO timestamp on **metadata**; this is what the hub reads)
- `kubectl.kubernetes.io/restartedAt` on the **pod template**
- memory limit `128Mi` after a successful heal

`result=skipped` means cooldown is still active. Wait 5 minutes or [Reset](#reset-for-a-second-run).

**Say**:
> "The runner bumped memory and annotated the Deployment. That annotation is how the
> hub will know not to run an AAP job."

---

### Step 3. Tell the hub

Heal must already be annotated. Then publish the OOM alert so you can watch the skip.

Dashboard: **Trigger OOM Demo** (site `edge-01`).

Or:

```bash
oc port-forward -n hub svc/hub-chatbot-service 8080:80
```

```bash
curl -s -X POST localhost:8080/api/demo/trigger \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"oom","site":"edge-01"}'
```

Use alert site id `edge-01`, not ManagedCluster name `edge-site-01`.

The healer does not publish its own Kafka "I healed" event. The dashboard (or live CLF)
is what starts the hub graph. Without an alert, AAP never runs, but you also never see
skip, Slack, or audit.

**Say**:
> "That alert is the same Kafka path as a live log. The hub still investigates and
> decides. Remediate will see the spoke already acted and will not launch Ansible."

---

### Step 4. Watch the hub skip Ansible

In agent logs you want:

```
Kafka alert received topic=system-alerts ...
Invoking workflow ...
Normalize node invoked
...
Remediate node invoked
Spoke fast-path healer already restarted edge-nginx (annotation adnr.io/fast-path-last-heal within cooldown)
```

**Must not appear:** `launch_job`, `scale-up-workers`, `restart-nginx`, a numeric `job_id`.

If MCP `get_deployment` fails, logs say `continuing to AAP` and Ansible **will** run.

Then:

```
Audit record published incident_id=... decision=remediate ...
Slack message sent ...
```

(or Slack fallback text if the bot is off)

**Say**:
> "Remediate checked the spoke Deployment, saw a fresh heal annotation, and skipped
> AAP. No job id, no playbook."

---

### Step 5. Prove the message back, and no Ansible

```bash
curl -s localhost:8080/api/integrations | jq '.incident_movie | .[-1]'
```

Expect:

- `failure_type`: `OOMKilled`
- `edge_site_id`: `edge-01`
- `remediation_action`: `fast_path_skip`
- `remediation_success`: `true`
- `fast_path_actuation`: `spoke`
- **no** `aap_job_id`

**Slack**: status **Resolved**. Resolution mentions the spoke healer and the
`adnr.io/fast-path-last-heal` annotation, not `Remediated via aap (job …)`.

**AAP Controller** (optional): no new `scale-up-workers` or `restart-nginx` job at that
timestamp.

**Say**:
> "Slack and the audit record show spoke actuation. Ansible never started."

---

### Step 6. Wrap up (optional contrast)

**Trigger CrashLoop Demo** without touching nginx memory. That path does not check the
fast-path annotation. You should see `launch_job` and a `job_id`. Known CrashLoop still
uses Ansible. OOM after a spoke heal does not.

**Say**:
> "The spoke healer is the fast path for this known-safe OOM. The hub stays in the loop
> for investigation and audit, and it does not duplicate the fix with AAP."

---

## Pause ArgoCD (patch fallback only)

Skip this when you use the in-pod `dd` trigger.

On the **hub** (GitOps namespace):

```bash
oc -n openshift-gitops get applications.argoproj.io | grep adnr-edge
APP=adnr-edge-edge-site-01
```

Pause ApplicationSet first. Otherwise it rewrites the Application and turns auto-sync
back on.

```bash
# 1. Stop ApplicationSet from reconciling Applications
oc -n openshift-gitops patch applicationset.argoproj.io adnr-edge --type merge \
  -p '{"spec":{"syncPolicy":{"applicationsSync":"paused"}}}'

# 2. Turn off automated sync on this spoke app
oc -n openshift-gitops patch application.argoproj.io "$APP" --type json \
  -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]'

oc -n openshift-gitops get application.argoproj.io "$APP" \
  -o jsonpath='{.spec.syncPolicy}{"\n"}'
```

You want `automated` gone (or empty). Then apply the memory patch in Step 1 fallback.

### Resume after the demo

```bash
oc -n openshift-gitops patch applicationset.argoproj.io adnr-edge --type json \
  -p '[{"op":"remove","path":"/spec/syncPolicy"}]'
```

The ApplicationSet will put `automated.prune` and `selfHeal` back on
`adnr-edge-edge-site-01`. Confirm:

```bash
oc -n openshift-gitops get application.argoproj.io adnr-edge-edge-site-01 \
  -o jsonpath='{.spec.syncPolicy.automated}{"\n"}'
```

---

## Reset for a second run

```bash
oc annotate deploy edge-nginx -n dark-noc-edge \
  adnr.io/fast-path-last-heal- adnr.io/fast-path-site-
```

If you used the patch fallback, restore chart memory (`32Mi` / `64Mi`) or unpause
ArgoCD and let it sync. Wait for a new pod at `64Mi` before the next `dd`.

---

## Timing Summary

| Step | Duration |
|------|----------|
| 0. Baseline | 30s |
| 1. In-pod OOM | 10s |
| 2. Healer (watcher poll + patch) | ~10s |
| 3. Dashboard / curl alert | 5s |
| 4. Hub graph + skip | 15-40s |
| 5. Audit / Slack proof | 10s |
| 6. Optional CrashLoop contrast | 30s |

---

## Troubleshooting

| What you see | Cause |
|---|---|
| Exec succeeds, no OOM | Limit is already `128Mi` from a prior heal. Reset annotations, wait for a new pod at `64Mi`, retry `dd`. |
| Watcher never posts | No recent `OOMKilled` lastState. Re-run `dd` against the **current** pod name. |
| Runner `skipped` | Cooldown. Wait 300s or clear the annotation. |
| Limit snaps back to `64Mi` before heal | Auto-sync without ignoreDifferences. Use the in-pod OOM trigger, or pause ArgoCD. |
| Hub launches AAP | Heal after the graph, annotation missing, or MCP `get_deployment` failed. Heal first, then trigger. |
| Dashboard OOM but nginx never restarts | Expected. The button is Kafka only. Step 1 is the real spoke issue. |
| Slack "not_in_channel" | Invite the bot to the demo channel. |

---

## Why the dashboard is still in this script

What launches AAP is Kafka `system-alerts` → hub agent → remediate → `launch_job`.
The healer only writes a Deployment annotation. It does not publish to Kafka or call
AAP.

The in-pod OOM is the real spoke issue. The dashboard button is a demo stand-in for a
live CLF error log so you can **watch** the hub skip. Dedicated healer Kafka events are
out of scope for v1. See [edge-fast-path-healer.md](edge-fast-path-healer.md)
(Known limitations).
