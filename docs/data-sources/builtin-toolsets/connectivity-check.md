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
```

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
