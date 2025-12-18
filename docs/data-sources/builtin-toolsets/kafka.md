# Kafka

By enabling this toolset, HolmesGPT will be able to fetch metadata from Kafka. This provides Holmes the ability to introspect into Kafka by listing consumers and topics or finding lagging consumer groups.

This toolset uses the AdminClient of the [confluent-kafka python library](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html#pythonclient-adminclient). Kafka's [Java API](https://docs.confluent.io/platform/current/installation/configuration/admin-configs.html) is also a good source of documentation.

## Configuration

```yaml-toolset-config
toolsets:
    kafka/admin:
        enabled: true
        config:
            kafka_clusters:
                - name: aks-prod-kafka
                  kafka_broker: kafka-1.aks-prod-kafka-brokers.kafka.svc:9095
                  kafka_username: kafka-plaintext-user
                  kafka_password: ******
                  kafka_sasl_mechanism: SCRAM-SHA-512
                  kafka_security_protocol: SASL_PLAINTEXT
                - name: gke-stg-kafka
                  kafka_broker: gke-kafka.gke-stg-kafka-brokers.kafka.svc:9095
                  kafka_username: kafka-plaintext-user
                  kafka_password: ****
                  kafka_sasl_mechanism: SCRAM-SHA-512
                  kafka_security_protocol: SASL_PLAINTEXT
```

Below is a description of the configuration field for each cluster:

| Config key | Description |
|------------|-------------|
| name | Give a meaningful name to your cluster. Holmes will use it to decide what cluster to look into. Names must be unique across all clusters. |
| kafka_broker | List of host/port pairs to use for establishing the initial connection to the Kafka cluster |
| kafka_username | Username for SASL authentication |
| kafka_password | Password for SASL authentication |
| kafka_sasl_mechanism | SASL mechanism (e.g., SCRAM-SHA-512) |
| kafka_security_protocol | Security protocol (e.g., SASL_PLAINTEXT) |

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| kafka_list_topics | List all Kafka topics |
| kafka_describe_topic | Get detailed information about a specific topic |
| kafka_list_consumers | List all consumer groups |
| kafka_describe_consumer | Get detailed information about a consumer group |
| kafka_consumer_lag | Check consumer lag for a consumer group |
