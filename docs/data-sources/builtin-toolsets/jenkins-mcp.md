# Jenkins (MCP)

Query Jenkins CI/CD data (jobs, builds, pipelines, console logs) via the PDI-hosted Jenkins MCP server.

## Capabilities

- List jobs and recent builds.
- Fetch specific build details (status, duration, SCM info).
- Retrieve build console logs for failure investigation.
- Trace pipeline stage failures.

## Configuration

Same pattern as [Atlassian (MCP)](atlassian-mcp.md). Populate `MCP_JENKINS_API_KEY` in the env-level secret via:

```bash
bash scripts/populate_mcp_keys.sh dev      # or: prod
```

Or create a per-project instance in the UI with `type: jenkins` and a per-project Secrets Manager ARN.

## Common Queries

```
"Why did the last deploy-checkout-api build fail?"
"Show me the most recent failing builds across all jobs in project X"
"Compare the console log of build #45 (failed) with build #44 (passed)"
"Which pipeline stage failed for last night's nightly build?"
```

## Troubleshooting

```bash
# Verify MCP_JENKINS_API_KEY is set
aws secretsmanager get-secret-value --secret-id holmesgpt-dev/mcp-api-keys \
  --profile pdi-platform-dev --region us-east-1 --query SecretString --output text \
  | python -c "import json,sys; d=json.loads(sys.stdin.read()); print('MCP_JENKINS_API_KEY set:', bool(d.get('MCP_JENKINS_API_KEY','')))"
```

| Symptom | Likely cause |
|---|---|
| `tool_count: 0` on success | Jenkins gateway reachable but no tools exposed for this key. |
| `HTTP 401` | Key invalid or expired. |
| `No credential source` | Instance has no `secret_arn` and `MCP_JENKINS_API_KEY` is empty. |
