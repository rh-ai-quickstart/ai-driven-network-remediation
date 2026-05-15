# Kafka Cross-Cluster Setup

Apache Kafka 4.1.2 in KRaft mode with mTLS for secure cross-cluster access.

> DEVELOPMENT CONFIGURATION**  
> Current settings are for **development only**. Before production, search for `TODO: PRODUCTION:` in `values.yaml` and review the [Production Checklist](#production-checklist) below.

## Quick Start

**Standalone deployment:**

```bash
make kafka-install NAMESPACE=<namespace>

# Uninstall
make kafka-uninstall NAMESPACE=<namespace>
```

**Deploy with hub (via helm-install):**

```bash
make helm-install ENABLE_KAFKA=true

# Uninstall
make helm-uninstall ENABLE_KAFKA=true
```

TLS certificates and the external route hostname are generated automatically by the Helm chart:
- The route hostname is discovered from the OpenShift ingress config (`ingresses.config.openshift.io/cluster`)
- Certificates are generated on first install via Helm's built-in crypto functions and persisted in secrets
- On upgrades, existing certificates are reused (not regenerated)

To override the auto-discovered hostname:

```bash
helm upgrade --install kafka hub/infra/kafka --set kafka.externalRoute.host=kafka.mydomain.com
```

## Access

**Internal (Hub cluster):**
- Kafka: `kafka:9092` (PLAINTEXT)
- Kafka UI: Access via OpenShift Route (automatically created)

**External (Cross-cluster):**
- Kafka: `<route-hostname>:443` (mTLS via OpenShift Route)

## Cross-Cluster Connection

### 1. Extract Client Certificates (Hub cluster)

```bash
make kafka-client-cert NAMESPACE=<namespace>
```

This creates:
- `ca.crt` - CA certificate
- `client.crt` - Client certificate
- `client.key` - Client private key

### 2. Deploy to Spoke Cluster

Create secret with certificates:

```bash
oc create secret generic kafka-client-certs \
  --from-file=ca.crt \
  --from-file=client.crt \
  --from-file=client.key \
  -n <spoke-namespace>
```

### 3. Configure Application

Mount the secret and configure Kafka client with PEM-based SSL:

```yaml
bootstrap.servers: <route-hostname>:443
security.protocol: SSL
ssl.keystore.type: PEM
ssl.keystore.key: <contents of client.key>
ssl.keystore.certificate.chain: <contents of client.crt>
ssl.truststore.type: PEM
ssl.truststore.certificates: <contents of ca.crt>
```

Or file-based (mount the secret and concatenate key + cert):

```yaml
bootstrap.servers: <route-hostname>:443
security.protocol: SSL
ssl.keystore.type: PEM
ssl.keystore.location: /certs/keystore.pem
ssl.truststore.type: PEM
ssl.truststore.location: /certs/ca.crt
```

Get the Route hostname:
```bash
oc get route kafka-external -n <namespace> -o jsonpath='{.spec.host}'
```

### 4. Python Example

Using `kafka-python-ng`:

```python
import os
from kafka import KafkaProducer, KafkaConsumer

BOOTSTRAP = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
SSL_OPTS = {
    "bootstrap_servers": BOOTSTRAP,
    "security_protocol": "SSL",
    "ssl_cafile": os.environ.get("KAFKA_SSL_CAFILE", "/certs/ca.crt"),
    "ssl_certfile": os.environ.get("KAFKA_SSL_CERTFILE", "/certs/client.crt"),
    "ssl_keyfile": os.environ.get("KAFKA_SSL_KEYFILE", "/certs/client.key"),
}

# Producer
producer = KafkaProducer(**SSL_OPTS, value_serializer=lambda v: v.encode())
producer.send("network-events", key=b"spoke-1", value='{"event": "link_down"}')
producer.flush()

# Consumer
consumer = KafkaConsumer(
    "network-events",
    **SSL_OPTS,
    group_id=os.environ.get("KAFKA_GROUP_ID", "spoke-consumer"),
    auto_offset_reset="earliest",
    value_deserializer=lambda v: v.decode(),
)
for msg in consumer:
    print(f"{msg.key}: {msg.value}")
```

## Configuration

Edit `hub/infra/kafka/values.yaml`:

- **Disable Kafka**: Set `kafka.enabled: false`
- **Disable Kafka UI**: Set `kafkaUI.enabled: false`
- **Disable external access**: Set `kafka.externalRoute.enabled: false`
- **Add topics**: Add to `kafka.topics` list
- **Resources**: Adjust `kafka.resources` for production
- **Storage**: Change `kafka.storage.size` or `storageClass`

## Production Checklist

Before deploying to production, review these critical settings:

1. **Cluster ID**: Generate unique ID with `kafka-storage.sh random-uuid` and update `kafka.clusterId`
2. **Replicas**: Set `kafka.replicas: 3` for HA (not 1)
3. **Resources**: Increase CPU/memory in `kafka.resources` (current: dev sizing)
4. **Retention**: Adjust topic retention in `kafka.topics` for production needs
5. **Storage class**: Verify `kafka.storage.storageClass` matches production storage

**Tip:** Search for `TODO: PRODUCTION:` comments in `values.yaml` for all settings requiring updates.
