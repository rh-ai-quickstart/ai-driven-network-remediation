# storage-full — Disk / PVC Full

## Symptoms
- Pod logs: `no space left on device`
- PVC usage at 100%: `oc df` or `df -h` inside pod
- Writes failing, database refusing new transactions

## Root Cause
Persistent volume filled by logs, database growth, or accumulated temp files.

## Remediation

### Check usage
```bash
oc exec <pod> -n <namespace> -- df -h
oc get pvc -n <namespace>
```

### Clear logs (nginx)
```bash
oc exec <pod> -n <namespace> -- find /var/log/nginx -name "*.log" -mtime +7 -delete
oc rollout restart deployment/nginx -n <namespace>
```

### Expand PVC (if storage class supports it)
```bash
oc patch pvc <pvc-name> -n <namespace> -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}'
```
