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
              # Internal destinations must be named here before they can be probed.
              # Accepts hostnames (matching subdomains), bare IPs, and CIDRs.
              allowed_hosts:
                - prometheus.monitoring.svc
                - 10.96.0.0/12
              block_internal_ips: true   # enforce the destination policy
              block_private_ips: false   # true = refuse private even if allowlisted
              max_probes: 60             # probes per window (0 disables)
              probe_window_seconds: 60
```

!!! warning "Probing internal addresses requires configuration"
    Private/RFC1918 destinations are refused unless you list them in
    `allowed_hosts`. If you use `tcp_check` against in-cluster services, add
    them (or their CIDR) to `allowed_hosts` — otherwise the probe is refused
    with a message naming this setting.

### SSRF protection

The probe target (`host`) is chosen by the LLM, and that choice can be
influenced by untrusted observability data (indirect prompt injection).
`tcp_check` returns distinguishable open / refused / filtered outcomes, which is
all that is needed to enumerate hosts and ports — so left unguarded it is a
blind internal-network scanner sitting inside your trust boundary. The toolset
therefore applies this policy:

- **Cloud metadata / loopback are blocked.** Requests to `169.254.0.0/16`
  (incl. `169.254.169.254`), loopback, link-local, multicast, reserved and
  unspecified addresses are rejected. The connection is made to the validated IP
  so DNS rebinding cannot redirect the probe.
- **Private/cluster destinations must be named.** RFC1918 and other private
  addresses are refused unless they match `allowed_hosts`. Probing internal
  services is the tool's legitimate purpose, so the operator names the ones that
  matter rather than the whole range being open. Entries may be hostnames
  (`db.internal` also matches `primary.db.internal`), bare IPs, or CIDRs
  (`10.96.0.0/12`).
- **An allowlist is exhaustive.** Once `allowed_hosts` is non-empty, public
  destinations outside it are refused too, and listed destinations are exempt
  from the internal-IP block so you can deliberately target a specific endpoint.
- **Public destinations stay reachable with no configuration**, so ordinary
  external connectivity checks work out of the box.
- **Probes are rate limited** to `max_probes` per `probe_window_seconds` so a
  broad allowlist cannot be swept quickly. The counter is per Holmes process and
  shared across concurrent investigations, so keep it above normal use; set
  `max_probes: 0` to disable.
- **Every probe is logged** — allowed and refused alike — so scanning is visible
  in the Holmes logs.
- `block_private_ips: true` refuses private destinations outright, even
  allowlisted ones.
- Set `block_internal_ips: false` only in trusted, isolated environments: it
  removes every range check and restores unrestricted probing.

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
