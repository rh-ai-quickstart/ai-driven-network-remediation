# aap-job-failure — Ansible Automation Platform Job Failed

## Symptoms
- AAP job status: `Failed` or `Error`
- Job output shows task failure with non-zero return code
- Event-Driven Ansible rule triggered but remediation did not complete

## Root Cause
Playbook task failure due to: unreachable target host, credential error,
missing variable, or underlying resource not in expected state.

## Remediation

### Check job output
```bash
curl -s -u admin:<token> https://<aap-url>/api/v2/jobs/<job-id>/stdout/?format=txt
```

### Re-launch job
```bash
curl -s -u admin:<token> -X POST \
  https://<aap-url>/api/v2/jobs/<job-id>/relaunch/ \
  -H "Content-Type: application/json" -d '{}'
```

### Check credential validity
```bash
curl -s -u admin:<token> https://<aap-url>/api/v2/credentials/<cred-id>/
```

### Check inventory host reachability
```bash
curl -s -u admin:<token> -X POST \
  https://<aap-url>/api/v2/inventory_sources/<id>/update/
```
