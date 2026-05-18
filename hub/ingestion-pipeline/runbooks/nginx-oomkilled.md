# nginx-oomkilled — Container OOM Kill

## Symptoms
- Pod status: `OOMKilled` (exit code 137)
- `kubectl/oc describe pod` shows `Last State: OOMKilled`
- Memory usage at or above `resources.limits.memory`

## Root Cause
The nginx container exceeded its memory limit and was killed by the Linux OOM killer.
Common causes: memory leak in worker processes, traffic spike, undersized limit.

## Remediation

### Immediate
```bash
oc set resources deployment nginx --limits=memory=512Mi --requests=memory=256Mi -n <namespace>
```

### Permanent
- Tune `worker_processes auto` and `worker_connections 1024` in nginx.conf
- Set memory alert at 80% of limit
- Add HPA to scale out before single-pod saturation
