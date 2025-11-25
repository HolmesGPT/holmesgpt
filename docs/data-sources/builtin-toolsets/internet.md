# Internet ✓

!!! info "Enabled by Default"
    This toolset is enabled by default and should typically remain enabled.

By enabling this toolset, HolmesGPT will be able to fetch webpages. This tool is beneficial if you provide Holmes with publicly accessible web-based runbooks.

## Configuration

```yaml
holmes:
    toolsets:
        internet:
            enabled: true
            config: # optional
              additional_headers:
                Authorization: Bearer ...
```

### Timeout Configuration

By default, the internet toolset uses a 5-second timeout for webpage requests. If you need to increase the timeout for slower websites, you can set the `INTERNET_TOOLSET_TIMEOUT_SECONDS` environment variable:

```bash
export INTERNET_TOOLSET_TIMEOUT_SECONDS=30
```

For Kubernetes deployments, add it to your Helm chart configuration:

```yaml
holmes:
    additionalEnvVars:
        - name: INTERNET_TOOLSET_TIMEOUT_SECONDS
          value: "30"
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| fetch_webpage | Fetch a webpage. Use this to fetch runbooks if they are present before starting your investigation (if no other tool like Confluence is more appropriate) |
