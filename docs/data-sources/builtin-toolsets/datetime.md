# Datetime ✓

--8<-- "snippets/enabled_by_default.md"

By enabling this toolset, HolmesGPT will be able to get the current UTC date and time. This feature should be kept enabled as it can be necessary for other toolsets that rely on dates and time.

The following built-in toolsets depend on `datetime`:

* Loki
* Prometheus
* Coralogix

## Configuration

```yaml
holmes:
    toolsets:
        datetime:
            enabled: true
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| get_current_time | Return current time information. Useful to build queries that require time information |
