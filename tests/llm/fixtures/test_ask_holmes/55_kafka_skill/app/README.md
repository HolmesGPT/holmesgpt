# Kafka Consumer Lag Simulation

This setup simulates a realistic Kafka environment with multiple producers and consumers, designed to demonstrate consumer lag scenarios for testing HolmesGPT's Kafka troubleshooting capabilities.

## System Architecture

### `finance` Topic
**Purpose**: Order-to-invoice processing pipeline
**Producer**: `orders-app` (fast producer)
**Consumer**: `invoices-app` (slow consumer - **LAG SIMULATION**)

- **orders-app**: Fast producer that generates customer orders every 100ms
  - Creates orders with product details, customer info, pricing
  - Sends to `finance` topic with high throughput

- **invoices-app**: Consumer that processes orders for invoice generation
  - Simulates email server processing (0.1-0.2 second delays per message)
  - Sends invoice emails to customers
  - **Intentionally slower than producer rate to create lag** for testing purposes
  - Consumer group: `invoices-processor`

### `payments` Topic
**Purpose**: Payment processing pipeline
**Producer**: `finance-app` (moderate producer)
**Consumer**: `accounting-app` (fast consumer)

- **finance-app**: Moderate-speed producer generating payment transactions every 1 second
  - Creates payment data with various payment methods, amounts, bank codes
  - Includes customer info and transaction references

- **accounting-app**: Fast consumer that processes payments efficiently
  - Calculates processing fees (70-130ms per message)
  - Performs risk scoring and database operations
  - Updates account balances quickly
  - Consumer group: `accounting-processor`

## Lag Simulation Details

The **`finance` topic intentionally creates consumer lag** because:
- **orders-app** produces messages every 100ms (fast)
- **invoices-app** takes 0.1-0.2 seconds per message + processing overhead (~150ms+ total)
- Producer rate (100ms) < Consumer rate (150ms+) creates growing lag that can be observed and investigated

The **`payments` topic operates normally** with:
- **finance-app** producing every 1 second
- **accounting-app** consuming in 70-130ms
- No significant lag under normal conditions

## Deployment

```bash
kubectl apply -f kafka-manifest.yaml
```

This deploys:
- Kafka broker with Zookeeper
- All 4 microservices (orders-app, invoices-app, finance-app, accounting-app)
- Creates the topic structure with appropriate partitioning

## Monitoring Lag

### Check finance topic lag (should show LAG > 0)
```bash
kubectl exec kafka-xxx -n ask-holmes-namespace-XX -- /opt/bitnami/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group invoices-processor
```

Expected output:
```
GROUP              TOPIC    PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
invoices-processor finance  0          177             758             581
```

### Check payments topic lag (should show LAG ≈ 0-1)
```bash
kubectl exec kafka-xxx -n ask-holmes-namespace-XX -- /opt/bitnami/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group accounting-processor
```

Expected output:
```
GROUP                TOPIC    PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
accounting-processor payments 0          83              88              5
```

## Testing Scenarios

This setup enables testing of:
1. **Consumer lag detection** - finance topic will show growing lag
2. **Consumer group status** - invoices-processor may show EMPTY state when slow
3. **Topic-to-consumer group mapping** - finding which groups consume from which topics
4. **Performance troubleshooting** - identifying slow consumers vs normal ones
