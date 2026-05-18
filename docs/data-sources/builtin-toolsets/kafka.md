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
                      password: ******
                      sasl_mechanism: SCRAM-SHA-512
                      security_protocol: SASL_PLAINTEXT
                    - name: gke-stg-kafka
                      broker: gke-kafka.gke-stg-kafka-brokers.kafka.svc:9095
                      username: kafka-plaintext-user
                      password: ****
                      sasl_mechanism: SCRAM-SHA-512
                      security_protocol: SASL_PLAINTEXT
    ```

=== "Holmes Helm Chart"

    Store credentials in a Kubernetes secret:

    ```bash
    kubectl create secret generic kafka-credentials \
      --from-literal=username=kafka-plaintext-user \
      --from-literal=password=<your-password> \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then reference them in your Helm values:

    ```yaml
    additionalEnvVars:
      - name: KAFKA_USERNAME
        valueFrom:
          secretKeyRef:
            name: kafka-credentials
            key: username
      - name: KAFKA_PASSWORD
        valueFrom:
          secretKeyRef:
            name: kafka-credentials
            key: password

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

    --8<-- "snippets/helm_upgrade_command.md"

=== "Robusta Helm Chart"

    Store credentials in a Kubernetes secret:

    ```bash
    kubectl create secret generic kafka-credentials \
      --from-literal=username=kafka-plaintext-user \
      --from-literal=password=<your-password> \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: KAFKA_USERNAME
          valueFrom:
            secretKeyRef:
              name: kafka-credentials
              key: username
        - name: KAFKA_PASSWORD
          valueFrom:
            secretKeyRef:
              name: kafka-credentials
              key: password

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

    --8<-- "snippets/helm_upgrade_command.md"

### mTLS — certificate files (Kubernetes mounted secrets)

Use this approach when certificates are mounted into the Holmes pod as Kubernetes secrets.

=== "Holmes CLI"

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

    For SASL+TLS (`SASL_SSL`) combine both sets of fields:

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

=== "Holmes Helm Chart"

    Create a Kubernetes secret containing the certificate files:

    ```bash
    kubectl create secret generic kafka-tls-certs \
      --from-file=ca.crt=/path/to/ca.crt \
      --from-file=client.pem=/path/to/client.pem \
      --from-file=client.key=/path/to/client.key \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then mount the secret and reference the paths in your Helm values:

    ```yaml
    additionalVolumes:
      - name: kafka-tls
        secret:
          secretName: kafka-tls-certs

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

    For SASL+TLS (`SASL_SSL`) add SASL credentials alongside the cert paths:

    ```yaml
    additionalEnvVars:
      - name: KAFKA_USERNAME
        valueFrom:
          secretKeyRef:
            name: kafka-credentials
            key: username
      - name: KAFKA_PASSWORD
        valueFrom:
          secretKeyRef:
            name: kafka-credentials
            key: password

    additionalVolumes:
      - name: kafka-tls
        secret:
          secretName: kafka-tls-certs

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

    --8<-- "snippets/helm_upgrade_command.md"

=== "Robusta Helm Chart"

    Create a Kubernetes secret containing the certificate files:

    ```bash
    kubectl create secret generic kafka-tls-certs \
      --from-file=ca.crt=/path/to/ca.crt \
      --from-file=client.pem=/path/to/client.pem \
      --from-file=client.key=/path/to/client.key \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalVolumes:
        - name: kafka-tls
          secret:
            secretName: kafka-tls-certs

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

    For SASL+TLS (`SASL_SSL`) add SASL credentials alongside the cert paths:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: KAFKA_USERNAME
          valueFrom:
            secretKeyRef:
              name: kafka-credentials
              key: username
        - name: KAFKA_PASSWORD
          valueFrom:
            secretKeyRef:
              name: kafka-credentials
              key: password

      additionalVolumes:
        - name: kafka-tls
          secret:
            secretName: kafka-tls-certs

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

    --8<-- "snippets/helm_upgrade_command.md"

### mTLS — base64-encoded inline certificates

Use this approach when certificates are passed as environment variables (e.g., from a secret manager or Kubernetes secret).

=== "Holmes CLI"

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

=== "Holmes Helm Chart"

    Store base64-encoded certificates in a Kubernetes secret:

    ```bash
    kubectl create secret generic kafka-tls-certs \
      --from-literal=ca.crt.b64="$(base64 < /path/to/ca.crt | tr -d '\n')" \
      --from-literal=client.pem.b64="$(base64 < /path/to/client.pem | tr -d '\n')" \
      --from-literal=client.key.b64="$(base64 < /path/to/client.key | tr -d '\n')" \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then expose them as environment variables in your Helm values:

    ```yaml
    additionalEnvVars:
      - name: KAFKA_CA_CERT_BASE64
        valueFrom:
          secretKeyRef:
            name: kafka-tls-certs
            key: ca.crt.b64
      - name: KAFKA_CLIENT_CERT_BASE64
        valueFrom:
          secretKeyRef:
            name: kafka-tls-certs
            key: client.pem.b64
      - name: KAFKA_CLIENT_KEY_BASE64
        valueFrom:
          secretKeyRef:
            name: kafka-tls-certs
            key: client.key.b64

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

    --8<-- "snippets/helm_upgrade_command.md"

=== "Robusta Helm Chart"

    Store base64-encoded certificates in a Kubernetes secret:

    ```bash
    kubectl create secret generic kafka-tls-certs \
      --from-literal=ca.crt.b64="$(base64 < /path/to/ca.crt | tr -d '\n')" \
      --from-literal=client.pem.b64="$(base64 < /path/to/client.pem | tr -d '\n')" \
      --from-literal=client.key.b64="$(base64 < /path/to/client.key | tr -d '\n')" \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: KAFKA_CA_CERT_BASE64
          valueFrom:
            secretKeyRef:
              name: kafka-tls-certs
              key: ca.crt.b64
        - name: KAFKA_CLIENT_CERT_BASE64
          valueFrom:
            secretKeyRef:
              name: kafka-tls-certs
              key: client.pem.b64
        - name: KAFKA_CLIENT_KEY_BASE64
          valueFrom:
            secretKeyRef:
              name: kafka-tls-certs
              key: client.key.b64

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

    --8<-- "snippets/helm_upgrade_command.md"

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
