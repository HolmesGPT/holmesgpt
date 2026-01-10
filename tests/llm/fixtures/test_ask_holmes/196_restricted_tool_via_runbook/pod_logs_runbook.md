# Pod Logs Investigation Runbook

This runbook guides you through investigating pod logs to diagnose application issues.

## Steps

1. First, identify the pod you need to investigate
2. Use the kubernetes logs tool to retrieve the pod logs
3. Analyze the logs for error messages or unusual patterns

## Common Log Patterns

- "Application started successfully" - Normal startup message
- Error messages typically contain "ERROR", "Exception", or "Failed"

## Troubleshooting Tips

- If logs are empty, check if the container has started
- Use the `--previous` flag to see logs from a previous container instance
- Check resource limits if the container is being OOMKilled
