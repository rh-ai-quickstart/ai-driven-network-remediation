# dns-failure — DNS Resolution Failure

## Symptoms
- Pods cannot resolve service names: `dial tcp: lookup <service>: no such host`
- `nslookup <service>.<namespace>.svc.cluster.local` fails from inside pod
- CoreDNS pods in `CrashLoopBackOff` or not ready

## Root Cause
CoreDNS unavailable, misconfigured, or overloaded. May also be a pod
DNS policy misconfiguration.

## Remediation

### Check CoreDNS
```bash
oc get pods -n openshift-dns
oc logs -l dns.operator.openshift.io/daemonset-dns -n openshift-dns --tail=50
```

### Restart CoreDNS
```bash
oc rollout restart daemonset/dns-default -n openshift-dns
```

### Test resolution from pod
```bash
oc exec <pod> -n <namespace> -- nslookup kubernetes.default.svc.cluster.local
```

### Check pod dnsPolicy
Ensure pod spec has `dnsPolicy: ClusterFirst` (default).
