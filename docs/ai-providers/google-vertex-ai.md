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

    **Create Kubernetes Secret:**
    ```bash
    # First, encode your service account JSON key
    kubectl create secret generic holmes-secrets \
      --from-file=google-credentials=path/to/service-account-key.json \
      --from-literal=vertexai-project="your-project-id" \
      --from-literal=vertexai-location="us-central1" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: VERTEXAI_PROJECT
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: vertexai-project
      - name: VERTEXAI_LOCATION
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: vertexai-location
      - name: GOOGLE_APPLICATION_CREDENTIALS
        value: "/etc/google-credentials/google-credentials"

    # Mount the credentials file (required for file-based authentication)
    # See: https://kubernetes.io/docs/concepts/storage/volumes/#secret
    additionalVolumes:
      - name: google-credentials
        secret:
          secretName: holmes-secrets
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

    # Optional: Set default model (use modelList key name)
    config:
      model: "vertex-gemini-pro"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    # First, encode your service account JSON key
    kubectl create secret generic robusta-holmes-secret \
      --from-file=google-credentials=path/to/service-account-key.json \
      --from-literal=vertexai-project="your-project-id" \
      --from-literal=vertexai-location="us-central1" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: VERTEXAI_PROJECT
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: vertexai-project
        - name: VERTEXAI_LOCATION
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: vertexai-location
        - name: GOOGLE_APPLICATION_CREDENTIALS
          value: "/etc/google-credentials/google-credentials"

      # Mount the credentials file (required for file-based authentication)
      # See: https://kubernetes.io/docs/concepts/storage/volumes/#secret
      additionalVolumes:
        - name: google-credentials
          secret:
            secretName: robusta-holmes-secret
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

      # Optional: Set default model (use modelList key name)
      config:
        model: "vertex-gemini-pro"  # This refers to the key name in modelList above
    ```

## Using CLI Parameters

You can also pass credentials directly as command-line parameters:

```bash
holmes ask "what pods are failing?" --model="vertex_ai/<your-vertex-model>" --api-key="your-service-account-key"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Google Vertex AI provider. Refer to [LiteLLM Google Vertex AI docs](https://litellm.vercel.app/docs/providers/vertex){:target="_blank"} for more details.
