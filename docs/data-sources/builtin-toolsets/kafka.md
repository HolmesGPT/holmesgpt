# Kafka

By enabling this toolset, HolmesGPT will be able to fetch metadata from Kafka. This provides Holmes the ability to introspect into Kafka by listing consumers and topics or finding lagging consumer groups.

This toolset uses the AdminClient of the [confluent-kafka python library](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html#pythonclient-adminclient). Kafka's [Java API](https://docs.confluent.io/platform/current/installation/configuration/admin-configs.html) is also a good source of documentation.

## Configuration

### SASL authentication

=== "Holmes CLI"

    ```yaml
    toolsets:
        kafka/admin:
            enabled: true
            config:
                clusters:
                    - name: aks-prod-kafka
                      broker: kafka-1.aks-prod-kafka-brokers.kafka.svc:9095
                      username: kafka-plaintext-user
                      password: "<your-password>"
                      sasl_mechanism: SCRAM-SHA-512
                      security_protocol: SASL_PLAINTEXT
                    - name: gke-stg-kafka
                      broker: gke-kafka.gke-stg-kafka-brokers.kafka.svc:9095
                      username: kafka-plaintext-user
                      password: "<your-password>"
                      sasl_mechanism: SCRAM-SHA-512
                      security_protocol: SASL_PLAINTEXT
    ```

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-kafka \
      --from-literal=KAFKA_USERNAME=kafka-plaintext-user \
      --from-literal=KAFKA_PASSWORD=<your-password> \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-kafka

    toolsets:
      kafka/admin:
        enabled: true
        config:
          clusters:
            - name: prod-kafka
              broker: kafka.prod.example.com:9095
              username: "{{ env.KAFKA_USERNAME }}"
              password: "{{ env.KAFKA_PASSWORD }}"
              sasl_mechanism: SCRAM-SHA-512
              security_protocol: SASL_PLAINTEXT
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-kafka \
      --from-literal=KAFKA_USERNAME=kafka-plaintext-user \
      --from-literal=KAFKA_PASSWORD=<your-password> \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-kafka

      toolsets:
        kafka/admin:
          enabled: true
          config:
            clusters:
              - name: prod-kafka
                broker: kafka.prod.example.com:9095
                username: "{{ env.KAFKA_USERNAME }}"
                password: "{{ env.KAFKA_PASSWORD }}"
                sasl_mechanism: SCRAM-SHA-512
                security_protocol: SASL_PLAINTEXT
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### mTLS — certificate files (Kubernetes mounted secrets)

Use this approach when certificates are mounted into the Holmes pod as Kubernetes secrets.

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      kafka/admin:
        enabled: true
        config:
          clusters:
            - name: prod-kafka
              broker: kafka.prod.example.com:9093
              security_protocol: SSL
              ssl_ca_cert_path: /etc/kafka-tls/ca.crt
              ssl_client_cert_path: /etc/kafka-tls/client.pem
              ssl_client_key_path: /etc/kafka-tls/client.key
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-kafka-tls \
      --from-file=ca.crt=/path/to/ca.crt \
      --from-file=client.pem=/path/to/client.pem \
      --from-file=client.key=/path/to/client.key \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    additionalVolumes:
      - name: kafka-tls
        secret:
          secretName: holmes-kafka-tls

    additionalVolumeMounts:
      - name: kafka-tls
        mountPath: /etc/kafka-tls
        readOnly: true

    toolsets:
      kafka/admin:
        enabled: true
        config:
          clusters:
            - name: prod-kafka
              broker: kafka.prod.example.com:9093
              security_protocol: SSL
              ssl_ca_cert_path: /etc/kafka-tls/ca.crt
              ssl_client_cert_path: /etc/kafka-tls/client.pem
              ssl_client_key_path: /etc/kafka-tls/client.key
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-kafka-tls \
      --from-file=ca.crt=/path/to/ca.crt \
      --from-file=client.pem=/path/to/client.pem \
      --from-file=client.key=/path/to/client.key \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalVolumes:
        - name: kafka-tls
          secret:
            secretName: holmes-kafka-tls

      additionalVolumeMounts:
        - name: kafka-tls
          mountPath: /etc/kafka-tls
          readOnly: true

      toolsets:
        kafka/admin:
          enabled: true
          config:
            clusters:
              - name: prod-kafka
                broker: kafka.prod.example.com:9093
                security_protocol: SSL
                ssl_ca_cert_path: /etc/kafka-tls/ca.crt
                ssl_client_cert_path: /etc/kafka-tls/client.pem
                ssl_client_key_path: /etc/kafka-tls/client.key
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

#### SASL+TLS (`SASL_SSL`)

For SASL+TLS (`SASL_SSL`) add SASL credentials alongside the cert paths:

In Kubernetes, this reuses the `holmes-kafka` secret created in the [SASL authentication](#sasl-authentication) section above.

In Kubernetes, this also reuses the `holmes-kafka-tls` secret created in the [mTLS — certificate files (Kubernetes mounted secrets)](#mtls-certificate-files-kubernetes-mounted-secrets) section above.

=== "Holmes CLI"

    Set the environment variables:

    ```bash
    export KAFKA_USERNAME=kafka-plaintext-user
    export KAFKA_PASSWORD=<your-password>
    ```

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      kafka/admin:
        enabled: true
        config:
          clusters:
            - name: prod-kafka
              broker: kafka.prod.example.com:9093
              security_protocol: SASL_SSL
              sasl_mechanism: SCRAM-SHA-512
              username: "{{ env.KAFKA_USERNAME }}"
              password: "{{ env.KAFKA_PASSWORD }}"
              ssl_ca_cert_path: /etc/kafka-tls/ca.crt
              ssl_client_cert_path: /etc/kafka-tls/client.pem
              ssl_client_key_path: /etc/kafka-tls/client.key
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-kafka

    additionalVolumes:
      - name: kafka-tls
        secret:
          secretName: holmes-kafka-tls

    additionalVolumeMounts:
      - name: kafka-tls
        mountPath: /etc/kafka-tls
        readOnly: true

    toolsets:
      kafka/admin:
        enabled: true
        config:
          clusters:
            - name: prod-kafka
              broker: kafka.prod.example.com:9093
              security_protocol: SASL_SSL
              sasl_mechanism: SCRAM-SHA-512
              username: "{{ env.KAFKA_USERNAME }}"
              password: "{{ env.KAFKA_PASSWORD }}"
              ssl_ca_cert_path: /etc/kafka-tls/ca.crt
              ssl_client_cert_path: /etc/kafka-tls/client.pem
              ssl_client_key_path: /etc/kafka-tls/client.key
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-kafka

      additionalVolumes:
        - name: kafka-tls
          secret:
            secretName: holmes-kafka-tls

      additionalVolumeMounts:
        - name: kafka-tls
          mountPath: /etc/kafka-tls
          readOnly: true

      toolsets:
        kafka/admin:
          enabled: true
          config:
            clusters:
              - name: prod-kafka
                broker: kafka.prod.example.com:9093
                security_protocol: SASL_SSL
                sasl_mechanism: SCRAM-SHA-512
                username: "{{ env.KAFKA_USERNAME }}"
                password: "{{ env.KAFKA_PASSWORD }}"
                ssl_ca_cert_path: /etc/kafka-tls/ca.crt
                ssl_client_cert_path: /etc/kafka-tls/client.pem
                ssl_client_key_path: /etc/kafka-tls/client.key
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### mTLS — base64-encoded inline certificates

Use this approach when certificates are passed as environment variables (e.g., from a secret manager or Kubernetes secret).

=== "Holmes CLI"

    Set the environment variables:

    ```bash
    export KAFKA_CA_CERT_BASE64="$(base64 < /path/to/ca.crt | tr -d '\n')"
    export KAFKA_CLIENT_CERT_BASE64="$(base64 < /path/to/client.pem | tr -d '\n')"
    export KAFKA_CLIENT_KEY_BASE64="$(base64 < /path/to/client.key | tr -d '\n')"
    ```

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      kafka/admin:
        enabled: true
        config:
          clusters:
            - name: prod-kafka
              broker: kafka.prod.example.com:9093
              security_protocol: SSL
              ssl_ca_cert: "{{ env.KAFKA_CA_CERT_BASE64 }}"
              ssl_client_cert: "{{ env.KAFKA_CLIENT_CERT_BASE64 }}"
              ssl_client_key: "{{ env.KAFKA_CLIENT_KEY_BASE64 }}"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-kafka-tls-base64 \
      --from-literal=KAFKA_CA_CERT_BASE64="$(base64 < /path/to/ca.crt | tr -d '\n')" \
      --from-literal=KAFKA_CLIENT_CERT_BASE64="$(base64 < /path/to/client.pem | tr -d '\n')" \
      --from-literal=KAFKA_CLIENT_KEY_BASE64="$(base64 < /path/to/client.key | tr -d '\n')" \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-kafka-tls-base64

    toolsets:
      kafka/admin:
        enabled: true
        config:
          clusters:
            - name: prod-kafka
              broker: kafka.prod.example.com:9093
              security_protocol: SSL
              ssl_ca_cert: "{{ env.KAFKA_CA_CERT_BASE64 }}"
              ssl_client_cert: "{{ env.KAFKA_CLIENT_CERT_BASE64 }}"
              ssl_client_key: "{{ env.KAFKA_CLIENT_KEY_BASE64 }}"
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-kafka-tls-base64 \
      --from-literal=KAFKA_CA_CERT_BASE64="$(base64 < /path/to/ca.crt | tr -d '\n')" \
      --from-literal=KAFKA_CLIENT_CERT_BASE64="$(base64 < /path/to/client.pem | tr -d '\n')" \
      --from-literal=KAFKA_CLIENT_KEY_BASE64="$(base64 < /path/to/client.key | tr -d '\n')" \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-kafka-tls-base64

      toolsets:
        kafka/admin:
          enabled: true
          config:
            clusters:
              - name: prod-kafka
                broker: kafka.prod.example.com:9093
                security_protocol: SSL
                ssl_ca_cert: "{{ env.KAFKA_CA_CERT_BASE64 }}"
                ssl_client_cert: "{{ env.KAFKA_CLIENT_CERT_BASE64 }}"
                ssl_client_key: "{{ env.KAFKA_CLIENT_KEY_BASE64 }}"
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Configuration fields

Below is a description of the configuration fields for each cluster entry:

| Config key | Required | Description |
|---|---|---|
| `name` | Yes | Unique name for this cluster. Holmes uses it to decide which cluster to query. |
| `broker` | Yes | Comma-separated list of `host:port` pairs for the initial broker connection. |
| `security_protocol` | No | Security protocol: `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, or `SASL_SSL`. |
| `sasl_mechanism` | No | SASL mechanism: `PLAIN`, `SCRAM-SHA-256`, or `SCRAM-SHA-512`. |
| `username` | No | Username for SASL authentication. |
| `password` | No | Password for SASL authentication. |
| `client_id` | No | Kafka client ID (default: `holmes-kafka-client`). |
| `ssl_ca_cert_path` | No | Path to the CA certificate file (PEM). Use when certs are mounted as Kubernetes secrets. |
| `ssl_client_cert_path` | No | Path to the client certificate file (PEM) for mTLS. |
| `ssl_client_key_path` | No | Path to the client private key file (PEM) for mTLS. |
| `ssl_ca_cert` | No | Base64-encoded CA certificate (PEM). Alternative to `ssl_ca_cert_path`. |
| `ssl_client_cert` | No | Base64-encoded client certificate (PEM) for mTLS. Alternative to `ssl_client_cert_path`. |
| `ssl_client_key` | No | Base64-encoded client private key (PEM) for mTLS. Alternative to `ssl_client_key_path`. |

When both a path field and its inline base64 counterpart are set, the path field takes precedence.

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| kafka_list_topics | List all Kafka topics |
| kafka_describe_topic | Get detailed information about a specific topic |
| kafka_list_consumers | List all consumer groups |
| kafka_describe_consumer | Get detailed information about a consumer group |
| kafka_consumer_lag | Check consumer lag for a consumer group |
