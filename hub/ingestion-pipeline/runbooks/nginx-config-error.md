# nginx-config-error — Invalid Configuration

## Symptoms
- nginx fails to reload: `nginx: [emerg] unknown directive`
- Pod stuck in `Error` or `Init:Error` state
- `nginx -t` returns non-zero exit code

## Root Cause
A ConfigMap or mounted file contains invalid nginx configuration syntax.
Often introduced by a faulty deployment or an incorrect template render.

## Remediation

### Validate config
```bash
oc exec <pod> -n <namespace> -- nginx -t
```

### Rollback
```bash
oc rollout undo deployment/nginx -n <namespace>
oc rollout status deployment/nginx -n <namespace>
```

### Fix and reapply ConfigMap
```bash
oc edit configmap nginx-config -n <namespace>
oc rollout restart deployment/nginx -n <namespace>
```
