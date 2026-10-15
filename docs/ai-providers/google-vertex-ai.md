# Google Vertex AI

Configure HolmesGPT to use Google Vertex AI with Gemini models.

## Setup

1. Create a Google Cloud project with [Vertex AI API enabled](https://cloud.google.com/vertex-ai/docs/start/introduction-unified-platform){:target="_blank"}
2. Create a service account with `Vertex AI User` role
3. Download the JSON key file

## Configuration

```yaml-toolset-config
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
---
secret:
  - --from-literal=VERTEXAI_PROJECT="your-project-id"
  - --from-literal=VERTEXAI_LOCATION="us-central1"
named-secrets:
  - name: holmes-google-vertex-ai-credentials
    keys:
      - --from-file=google-credentials=path/to/service-account-key.json
cli: |
  ```bash
  export VERTEXAI_PROJECT="your-project-id"
  export VERTEXAI_LOCATION="us-central1"
  export GOOGLE_APPLICATION_CREDENTIALS="path/to/service-account-key.json"

  holmes ask "what pods are failing?" --model="vertex_ai/<your-vertex-model>"
  ```
```

## Using CLI Parameters

You can also pass credentials directly as command-line parameters:

```bash
holmes ask "what pods are failing?" --model="vertex_ai/<your-vertex-model>" --api-key="your-service-account-key"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Google Vertex AI provider. Refer to [LiteLLM Google Vertex AI docs](https://litellm.vercel.app/docs/providers/vertex){:target="_blank"} for more details.
