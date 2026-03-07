# Runbooks

Runbooks are step-by-step troubleshooting guides that Holmes follows when investigating issues. When a user asks a question or an alert fires, Holmes automatically matches relevant runbooks from its catalog and fetches them using the `fetch_runbook` tool. It then follows the runbook instructions step-by-step, calling tools to gather data and reporting results for each step.

Runbooks work with all Holmes interfaces — the CLI (`ask` and `investigate` commands), the HTTP server, and the Python SDK.

## How It Works

1. Holmes receives a question or alert
2. Holmes compares the issue against runbook descriptions in the catalog
3. If a runbook matches, Holmes fetches it with the `fetch_runbook` tool
4. Holmes follows the runbook steps, calling tools to gather data at each step
5. Holmes reports findings with a checklist showing completed and skipped steps

## Built-in Runbooks

Holmes ships with a built-in runbook catalog at `holmes/plugins/runbooks/`. These are available automatically — no configuration needed.

## Custom Runbook Catalogs

You can add your own runbooks by creating a catalog and pointing Holmes to it.

### Creating a Catalog

A catalog consists of a `catalog.json` index file and one or more markdown runbook files:

```
my-runbooks/
├── catalog.json
├── database/
│   ├── postgres_troubleshooting.md
│   └── redis_connection_issues.md
└── networking/
    └── dns_resolution.md
```

**`catalog.json`:**

```json
{
  "catalog": [
    {
      "id": "postgres-troubleshooting.md",
      "update_date": "2026-01-15",
      "description": "Troubleshooting PostgreSQL connection and performance issues",
      "link": "database/postgres_troubleshooting.md"
    },
    {
      "id": "redis-connection-issues.md",
      "update_date": "2026-01-15",
      "description": "Diagnosing Redis connection failures and timeout issues",
      "link": "database/redis_connection_issues.md"
    },
    {
      "id": "dns-resolution.md",
      "update_date": "2026-01-15",
      "description": "Troubleshooting DNS resolution failures in Kubernetes clusters",
      "link": "networking/dns_resolution.md"
    }
  ]
}
```

Each entry has:

- **`id`**: Unique identifier (typically the filename)
- **`update_date`**: Last updated date (`YYYY-MM-DD`)
- **`description`**: Used by the LLM to match the runbook to user questions — make this descriptive
- **`link`**: Relative path from `catalog.json` to the markdown file

### Writing a Runbook

Runbooks are markdown files with a structured format that guides Holmes through troubleshooting steps:

```markdown
# PostgreSQL Connection Troubleshooting

## Goal
Diagnose and resolve PostgreSQL database connection issues.
Follow the workflow steps sequentially.

## Workflow

1. **Check database pod status**
   * Verify pods are running and not restarting
   * Check resource usage (CPU, memory)

2. **Test database connectivity**
   * Verify the connection string and credentials
   * Check network policies and service endpoints

3. **Examine database logs**
   * Look for authentication failures
   * Check for max connection limit errors

4. **Review client configuration**
   * Validate connection pool settings
   * Check timeout configurations

## Synthesize Findings
Correlate the outputs from each step to identify the root cause.

## Recommended Remediation Steps
* **Authentication failures**: Verify credentials in the application Secret
* **Connection limit**: Increase `max_connections` or add connection pooling
* **Network issues**: Check NetworkPolicies and DNS resolution
```

The key sections are:

- **Goal**: What the runbook addresses
- **Workflow**: Sequential diagnostic steps Holmes will execute using its tools
- **Synthesize Findings**: How to interpret combined results
- **Recommended Remediation Steps**: Solutions based on findings

### Configuring Custom Catalogs

=== "Config File"

    Add catalog paths to `~/.holmes/config.yaml`:

    ```yaml
    custom_runbook_catalogs:
      - /path/to/my-runbooks/catalog.json
      - /path/to/team-runbooks/catalog.json
    ```

=== "Helm Chart"

    Mount your catalog files and reference them in values:

    ```yaml
    customRunbookCatalogs:
      - /etc/holmes/runbooks/catalog.json
    ```

=== "Python SDK"

    ```python
    from pathlib import Path

    from holmes.config import Config

    config = Config.load_from_file(
        config_file=Path("~/.holmes/config.yaml").expanduser(),
    )
    # custom_runbook_catalogs is read from the config file
    catalog = config.get_runbook_catalog()
    ```

Multiple catalogs are merged — entries from all catalogs are combined with the built-in catalog.

## Common Use Cases

```
Why is my PostgreSQL database connection timing out?
```

```
Investigate the OOMKilled alert on the payments service
```

```
Help me troubleshoot DNS resolution failures in the staging cluster
```

## Troubleshooting

```bash
# Verify your catalog.json is valid JSON
python3 -c "import json; json.load(open('/path/to/catalog.json'))"

# Check Holmes logs for runbook loading errors
# Look for "Error decoding JSON" or "Custom catalog file not found"
holmes ask "test question" -v
```
