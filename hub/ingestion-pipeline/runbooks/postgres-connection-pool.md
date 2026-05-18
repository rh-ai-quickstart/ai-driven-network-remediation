# postgres-connection-pool — Connection Pool Exhausted

## Symptoms
- Application logs: `remaining connection slots are reserved for replication`
- `FATAL: sorry, too many clients already`
- New connections timing out or refused

## Root Cause
PostgreSQL `max_connections` reached. Common causes: connection leak in
application, missing connection pooler (PgBouncer), or sudden traffic spike.

## Remediation

### Check active connections
```bash
oc exec -it <postgres-pod> -n <namespace> -- \
  psql -U postgres -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
```

### Kill idle connections
```bash
oc exec -it <postgres-pod> -n <namespace> -- \
  psql -U postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND query_start < now()-interval '10 min';"
```

### Increase max_connections (requires restart)
Edit CloudNativePG cluster CR: `postgresql.parameters.max_connections: "200"`

### Long-term: deploy PgBouncer
Add PgBouncer sidecar or pooler CR in CloudNativePG to manage connection pooling.
