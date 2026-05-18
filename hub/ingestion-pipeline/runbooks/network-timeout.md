# network-timeout — Connection Timeout

## Symptoms
- HTTP 504 Gateway Timeout or connection refused errors
- `curl` to service IP times out
- Logs show `upstream timed out (110: Connection timed out)`

## Root Cause
Upstream service unreachable: pod not ready, service selector mismatch,
NetworkPolicy blocking traffic, or upstream overloaded.

## Remediation

### Check endpoints
```bash
oc get endpoints <service> -n <namespace>
oc describe service <service> -n <namespace>
```

### Check NetworkPolicy
```bash
oc get networkpolicy -n <namespace>
```

### Restart upstream pods
```bash
oc rollout restart deployment/<upstream> -n <namespace>
```

### Increase nginx upstream timeout (temporary)
Add to nginx.conf: `proxy_read_timeout 120s; proxy_connect_timeout 30s;`
