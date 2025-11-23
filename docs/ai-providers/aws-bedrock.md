# AWS Bedrock

Configure HolmesGPT to use AWS Bedrock foundation models.

!!! tip "Which Model to Use"
    We highly recommend using Sonnet 4.0 or Sonnet 4.5 as they give the best results by far. See examples below for configuration.

## Setup

### Prerequisites

1. **Install boto3**: AWS Bedrock requires boto3 version 1.28.57 or higher:
   ```bash
   pip install "boto3>=1.28.57"
   ```

2. **AWS credentials**: Ensure you have AWS credentials configured with access to Bedrock models. See [AWS Docs](https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started.html){:target="_blank"}.

## Configuration

=== "Holmes CLI"

    ```bash
    export AWS_REGION_NAME="us-east-1"  # Replace with your region
    export AWS_ACCESS_KEY_ID="your-access-key"
    export AWS_SECRET_ACCESS_KEY="your-secret-key"
    export EXTRA_HEADERS="{\"anthropic-beta\": \"context-1m-2025-08-07\"}" # Optional, beta 1M context window support, for Claude Sonnet 4 model.

    holmes ask "what pods are failing?" --model="bedrock/<your-bedrock-model>"
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=aws-access-key-id="AKIA..." \
      --from-literal=aws-secret-access-key="your-secret-key" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: AWS_ACCESS_KEY_ID
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: aws-access-key-id
      - name: AWS_SECRET_ACCESS_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: aws-secret-access-key

    # Configure at least one model using modelList
    modelList:
      bedrock-claude-35-sonnet:
        aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
        aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
        aws_region_name: us-east-1
        model: bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0
        temperature: 1

      bedrock-claude-sonnet-4:
        aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
        aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
        aws_region_name: eu-south-2
        model: bedrock/eu.anthropic.claude-sonnet-4-20250514-v1:0
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled

      bedrock-claude-sonnet-4-1M-context:
        aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
        aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
        aws_region_name: eu-south-2
        model: bedrock/eu.anthropic.claude-sonnet-4-20250514-v1:0
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled
        extra_headers:
          anthropic-beta: context-1m-2025-08-07

    # Optional: Set default model (use modelList key name)
    config:
      model: "bedrock-claude-35-sonnet"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=aws-access-key-id="AKIA..." \
      --from-literal=aws-secret-access-key="your-secret-key" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: AWS_ACCESS_KEY_ID
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: aws-access-key-id
        - name: AWS_SECRET_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: aws-secret-access-key

      # Configure at least one model using modelList
      modelList:
        bedrock-claude-35-sonnet:
          aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
          aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
          aws_region_name: us-east-1
          model: bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0
          temperature: 1

        bedrock-claude-sonnet-4:
          aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
          aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
          aws_region_name: eu-south-2
          model: bedrock/eu.anthropic.claude-sonnet-4-20250514-v1:0
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled

        bedrock-claude-sonnet-4-1M-context:
          aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
          aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
          aws_region_name: eu-south-2
          model: bedrock/eu.anthropic.claude-sonnet-4-20250514-v1:0
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled
          extra_headers:
            anthropic-beta: context-1m-2025-08-07

      # Optional: Set default model (use modelList key name)
      config:
        model: "bedrock-claude-35-sonnet"  # This refers to the key name in modelList above
    ```

### Finding Your AWS Credentials

If the AWS CLI is already configured on your machine, you may be able to find the above values with:

```bash
cat ~/.aws/credentials ~/.aws/config
```

### Finding Available Models

To list models your account can access (replacing `us-east-1` with the relevant region):

```bash
aws bedrock list-foundation-models --region=us-east-1 | grep modelId
```

**Important**: Different models are available in different regions. For example, Claude Opus is only available in us-west-2.

### Model Name Examples
Be sure to replace `<your-bedrock-model>` with a model you have access to, such as `anthropic.claude-opus-4-1-20250805-v1:0` or `anthropic.claude-sonnet-4-20250514-v1:0`

## Additional Resources

HolmesGPT uses the LiteLLM API to support AWS Bedrock provider. Refer to [LiteLLM Bedrock docs](https://litellm.vercel.app/docs/providers/bedrock){:target="_blank"} for more details.
