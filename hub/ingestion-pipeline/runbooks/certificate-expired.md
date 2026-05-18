# certificate-expired — TLS Certificate Expired

## Symptoms
- `curl` returns `SSL certificate problem: certificate has expired`
- Browser shows `NET::ERR_CERT_DATE_INVALID`
- nginx logs: `SSL_do_handshake() failed (SSL: error:0A000416)`

## Root Cause
TLS certificate reached its expiry date. Common in self-managed certs
not covered by cert-manager auto-renewal.

## Remediation

### Check expiry
```bash
oc get secret <tls-secret> -n <namespace> -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -enddate
```

### Renew with cert-manager (if installed)
```bash
oc annotate certificate <cert-name> -n <namespace> \
  cert-manager.io/issue-once="$(date +%s)"
```

### Manual rotation
```bash
oc create secret tls <tls-secret> --cert=new.crt --key=new.key \
  -n <namespace> --dry-run=client -o yaml | oc apply -f -
oc rollout restart deployment/nginx -n <namespace>
```
