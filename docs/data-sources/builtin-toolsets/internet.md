# Internet ✓

!!! info "Internet access is off by default"
    The toolset is loaded by default, but Holmes does not fetch a webpage on its own. Each fetch needs your approval unless you list the host in `allowed_hosts`.

This toolset lets HolmesGPT fetch webpages, for example web-based runbooks or a vendor's documentation.

## Approval

Holmes chooses which URL to fetch, and text it reads during an investigation can influence that choice. So a fetch goes one of three ways:

- **The host is in `allowed_hosts`:** Holmes fetches it without asking.
- **`allowed_hosts` is empty (the default):** Holmes asks you to approve the URL. Where nobody can approve (alert investigations, scheduled prompts, API calls without tool approval), the fetch is refused.
- **`allowed_hosts` is set and the host is not in it:** the fetch is refused.

An approval covers the host you saw. If the page redirects to a different host, Holmes stops and asks about the new URL.

To let Holmes read your runbooks without asking, list their hosts in `allowed_hosts`. To let Holmes fetch any public host without asking, set `require_approval: false`.

## Configuration

```yaml-toolset-config
toolsets:
    internet:
        enabled: true
        config: # optional
          additional_headers:
            Authorization: Bearer ...
          allowed_hosts: # hosts Holmes may fetch without asking
            - docs.example.com
          require_approval: true # default; with no allowed_hosts, ask before each fetch
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

The internal-IP block always runs (the host is resolved and checked before any request is
made). The connection *pin* additionally protects direct connections against DNS rebinding;
when an outbound HTTP(S) proxy is configured, the proxy performs its own resolution, so the
pin cannot apply to that hop — set `allowed_hosts` if you need to constrain what a proxied
deployment can reach.

### Timeout Configuration

By default, the internet toolset uses a 5-second timeout for webpage requests. If you need to increase the timeout for slower websites, you can set the `INTERNET_TOOLSET_TIMEOUT_SECONDS` environment variable:

```yaml-toolset-config
additionalEnvVars:
  - name: INTERNET_TOOLSET_TIMEOUT_SECONDS
    value: "30"
---
cli: |
  Set the environment variable:

  ```bash
  export INTERNET_TOOLSET_TIMEOUT_SECONDS=30
  ```
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| fetch_webpage | Fetch a webpage, subject to the approval rules above |
