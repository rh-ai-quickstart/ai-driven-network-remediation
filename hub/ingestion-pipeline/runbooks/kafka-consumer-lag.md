# kafka-consumer-lag — Consumer Group Lag

## Symptoms
- Consumer group lag growing continuously
- Messages piling up in topic partition
- Downstream processing delayed or stalled

## Root Cause
Consumer processing too slow, consumer crashed/disconnected, or topic partition
count too low for the load. Also triggered by a rebalance storm.

## Remediation

### Check lag
```bash
oc exec -it <kafka-pod> -n <namespace> -- \
  bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group <group-id>
```

### Restart consumer deployment
```bash
oc rollout restart deployment/<consumer> -n <namespace>
```

### Scale consumers (must not exceed partition count)
```bash
oc scale deployment/<consumer> --replicas=3 -n <namespace>
```

### Increase partitions
```bash
oc exec -it <kafka-pod> -n <namespace> -- \
  bin/kafka-topics.sh --bootstrap-server localhost:9092 \
  --alter --topic <topic> --partitions 6
```
