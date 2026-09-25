# Google Vertex AI

Configure HolmesGPT to use Google Vertex AI with Gemini models.

## Setup

1. Create a Google Cloud project with [Vertex AI API enabled](https://cloud.google.com/vertex-ai/docs/start/introduction-unified-platform){:target="_blank"}
2. Create a service account with `Vertex AI User` role
3. Download the JSON key file

## Configuration

=== "Holmes CLI"

    ```bash
    export VERTEXAI_PROJECT="your-project-id"
    export VERTEXAI_LOCATION="us-central1"
    export GOOGLE_APPLICATION_CREDENTIALS="path/to/service-account-key.json"

    holmes ask "what pods are failing?" --model="vertex_ai/<your-vertex-model>"
    ```

=== "Holmes Helm Chart"

    Create the Kubernetes secrets in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-google-vertex-ai \
      --from-literal=VERTEXAI_PROJECT="your-project-id" \
      --from-literal=VERTEXAI_LOCATION="us-central1" \
      -n <namespace>

    kubectl create secret generic holmes-google-vertex-ai-credentials \
      --from-file=google-credentials=path/to/service-account-key.json \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-google-vertex-ai

    additionalEnvVars:
      - name: GOOGLE_APPLICATION_CREDENTIALS
        value: "/etc/google-credentials/google-credentials"
      # Optional: Set default model (use modelList key name)
      - name: MODEL
        value: "vertex-gemini-pro"  # This refers to the key name in modelList below

    # Mount the credentials file (required for file-based authentication)
    # See: https://kubernetes.io/docs/concepts/storage/volumes/#secret
    additionalVolumes:
      - name: google-credentials
        secret:
          secretName: holmes-google-vertex-ai-credentials
          items:
            - key: google-credentials
              path: google-credentials

    additionalVolumeMounts:
      - name: google-credentials
        mountPath: /etc/google-credentials
        readOnly: true

    # Configure at least one model using modelList
    modelList:
      vertex-gemini-pro:
        vertex_project: "{{ env.VERTEXAI_PROJECT }}"
        vertex_location: "{{ env.VERTEXAI_LOCATION }}"
        model: vertex_ai/gemini-pro
        temperature: 1

      vertex-gemini-flash:
        vertex_project: "{{ env.VERTEXAI_PROJECT }}"
        vertex_location: "{{ env.VERTEXAI_LOCATION }}"
        model: vertex_ai/gemini-1.5-flash
        temperature: 1
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmesgpt robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create the Kubernetes secrets in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-google-vertex-ai \
      --from-literal=VERTEXAI_PROJECT="your-project-id" \
      --from-literal=VERTEXAI_LOCATION="us-central1" \
      -n <namespace>

    kubectl create secret generic holmes-google-vertex-ai-credentials \
      --from-file=google-credentials=path/to/service-account-key.json \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-google-vertex-ai

      additionalEnvVars:
        - name: GOOGLE_APPLICATION_CREDENTIALS
          value: "/etc/google-credentials/google-credentials"
        # Optional: Set default model (use modelList key name)
        - name: MODEL
          value: "vertex-gemini-pro"  # This refers to the key name in modelList below

      # Mount the credentials file (required for file-based authentication)
      # See: https://kubernetes.io/docs/concepts/storage/volumes/#secret
      additionalVolumes:
        - name: google-credentials
          secret:
            secretName: holmes-google-vertex-ai-credentials
            items:
              - key: google-credentials
                path: google-credentials

      additionalVolumeMounts:
        - name: google-credentials
          mountPath: /etc/google-credentials
          readOnly: true

      # Configure at least one model using modelList
      modelList:
        vertex-gemini-pro:
          vertex_project: "{{ env.VERTEXAI_PROJECT }}"
          vertex_location: "{{ env.VERTEXAI_LOCATION }}"
          model: vertex_ai/gemini-pro
          temperature: 1

        vertex-gemini-flash:
          vertex_project: "{{ env.VERTEXAI_PROJECT }}"
          vertex_location: "{{ env.VERTEXAI_LOCATION }}"
          model: vertex_ai/gemini-1.5-flash
          temperature: 1
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Using CLI Parameters

You can also pass credentials directly as command-line parameters:

```bash
holmes ask "what pods are failing?" --model="vertex_ai/<your-vertex-model>" --api-key="your-service-account-key"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Google Vertex AI provider. Refer to [LiteLLM Google Vertex AI docs](https://litellm.vercel.app/docs/providers/vertex){:target="_blank"} for more details.
