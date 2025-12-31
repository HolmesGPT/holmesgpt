# Connectivity Check ✓

!!! info "Enabled by Default"
    This toolset is enabled by default and should typically remain enabled.

The connectivity check toolset provides basic network connectivity verification tools. It allows HolmesGPT to test if specific hosts and ports are reachable using both TCP socket connections and HTTP/HTTPS requests.

This toolset is useful for troubleshooting network connectivity issues, verifying service availability, and validating endpoints during incident investigation.

!!! warning "Strict Implementation"
    These tools are intentionally strict and minimal:
    
    - **No redirect following**: HTTP checks do not follow redirects automatically
    - **Limited user agents**: Only "none" (no User-Agent header) or "browser" (preset browser-like header) 
    - **Basic HTTP methods**: Only GET requests are supported
    - **No authentication**: No support for authentication headers or credentials
    - **Simple validation**: Focused on basic reachability rather than comprehensive HTTP testing
    
    For advanced HTTP testing, consider using dedicated tools or the bash toolset with curl.

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
| http_check | Check if an HTTP or HTTPS endpoint is reachable and return the status code. Supports custom paths, timeouts, and user-agent modes |
| tcp_check | Check if a TCP socket can be opened to a host and port. Useful for testing basic network connectivity to services |

## Examples

### HTTP Connectivity Check
```
Can you check if https://example.com is accessible?
```

### TCP Port Check
```
Check if the database server at db.example.com port 5432 is reachable.
```

### Service Health Verification
```
Verify connectivity to the API endpoint at api.internal.com:8080/health
```
