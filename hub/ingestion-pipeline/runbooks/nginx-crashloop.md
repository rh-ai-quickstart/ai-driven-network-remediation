# nginx-crashloop — CrashLoopBackOff

## Symptoms
- Pod status: `CrashLoopBackOff`
- Restart count increasing with exponential backoff (10s → 20s → 40s … 5m)
- Exit code 1 or 137 in previous container state

## Root Cause
nginx fails to start or crashes shortly after start. Typical causes:
invalid configuration, missing mounted secret/configmap, or OOM on startup.

## Remediation

### Diagnose
```bash
oc logs <pod> --previous -n <namespace>
oc describe pod <pod> -n <namespace>
```

### Fix config error
```bash
oc exec <pod> -n <namespace> -- nginx -t
oc rollout undo deployment/nginx -n <namespace>
```

### Fix missing secret
```bash
oc get secret <secret-name> -n <namespace>
oc create secret generic <secret-name> --from-literal=key=value -n <namespace>
```
