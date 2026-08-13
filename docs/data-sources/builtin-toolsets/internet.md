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
              allowed_hosts: # optional allowlist (see SSRF protection below)
                - docs.example.com
              block_internal_ips: true # default; blocks internal/non-routable targets
```

### SSRF protection

Because the LLM chooses which URL `fetch_webpage` retrieves — and that choice can be
influenced by untrusted observability data (indirect prompt injection) — the toolset
guards every request:

- **Only `http`/`https` URLs** are fetched; other schemes (`file://`, `ftp://`, …) are rejected.
- **Internal targets are blocked.** The host is resolved and the request is refused if it
  points at a loopback, link-local (including `169.254.169.254` cloud metadata), private
  (RFC1918), reserved, multicast or unspecified address. Redirects are re-validated per hop,
  and the connection is pinned to the validated IP to defeat DNS rebinding. Set
  `block_internal_ips: false` only in trusted, isolated environments.
- **Optional allowlist.** When `allowed_hosts` is set, only those hosts (and their
  subdomains) may be fetched. Allowlisted hosts are exempt from the internal-IP block, so
  you can deliberately point the tool at a known internal endpoint.
- **Auth headers are only sent to allowlisted hosts.** `additional_headers` are forwarded
  only when the host is in `allowed_hosts` (and are stripped on cross-host redirects), so
  configured credentials cannot leak to an arbitrary host the model picks.

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
