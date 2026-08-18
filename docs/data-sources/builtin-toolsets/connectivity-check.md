# Connectivity Check ✓

!!! info "Enabled by Default"
    This toolset is enabled by default and should typically remain enabled.

The connectivity check toolset provides basic TCP network connectivity verification. It allows HolmesGPT to test if specific hosts and ports are reachable using TCP socket connections.

This toolset is useful for troubleshooting network connectivity issues, verifying service availability, and validating that TCP services are listening on expected ports.

## Configuration

```yaml
holmes:
    toolsets:
        connectivity_check:
            enabled: true
            config: # optional
              allowed_hosts: []      # if set, only these hosts may be probed
              block_internal_ips: true  # block metadata/loopback/link-local targets
              block_private_ips: false  # also block private/RFC1918 (off: cluster IPs stay reachable)
```

### SSRF protection

The probe target (`host`) is chosen by the LLM, and that choice can be
influenced by untrusted observability data (indirect prompt injection). Left
unguarded, `tcp_check` is a blind internal-network scanner. The toolset
therefore refuses the dangerous targets by default:

- **Cloud metadata / loopback are blocked.** Requests to `169.254.0.0/16`
  (incl. `169.254.169.254`), loopback, link-local, multicast, reserved and
  unspecified addresses are rejected. The connection is made to the validated IP
  so DNS rebinding cannot redirect the probe.
- **Private/cluster IPs stay reachable by default.** Checking connectivity to
  internal cluster services (RFC1918) is the tool's main purpose, so those are
  allowed. Set `block_private_ips: true` to restrict the tool to public hosts.
- **Optional allowlist.** When `allowed_hosts` is set, only those hosts (and
  subdomains) may be probed, and they are exempt from the internal-IP block.
- Set `block_internal_ips: false` only in trusted, isolated environments.

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| tcp_check | Check if a TCP socket can be opened to a host and port. Useful for testing basic network connectivity to services |

## Examples

### TCP Port Check
```
Check if the database server at db.example.com port 5432 is reachable.
```

### Service Connectivity Verification
```
Test if the Redis service at redis.internal.com:6379 is accepting connections.
```

### Web Server Port Test
```
Check if port 80 is open on web.example.com.
```
