# Custom Runbook Catalogs

This directory demonstrates how to create custom runbook catalogs for HolmesGPT.

## Structure

A custom runbook catalog consists of:

1. **catalog.json** - Index file listing all available runbooks
2. **Markdown files** - Individual runbook content files

## catalog.json Format

```json
{
  "catalog": [
    {
      "id": "unique-runbook-id.md",
      "update_date": "2025-11-27",
      "description": "Description used by LLM to match runbook to issues",
      "link": "relative/path/to/runbook.md"
    }
  ]
}
```

## Markdown Runbook Format

Each markdown runbook should follow this structure:

1. **Goal** - Define what the runbook addresses
2. **Workflow** - Step-by-step diagnostic procedures
3. **Synthesize Findings** - How to interpret results
4. **Recommended Remediation Steps** - Solutions based on findings

See `example_troubleshooting.md` for a complete example.

## Using Custom Runbook Catalogs

### Option 1: Config File

Add to `~/.holmes/config.yaml`:

```yaml
custom_runbook_catalogs: ["/path/to/your/catalog.json"]
```

### Option 2: Command Line

```bash
# This feature is configured via the config file
# The catalog.json path should point to your custom catalog
```

## Directory Organization

You can organize runbooks in subdirectories:

```
my-custom-runbooks/
├── catalog.json
├── database/
│   ├── postgres_troubleshooting.md
│   └── mongodb_troubleshooting.md
└── networking/
    ├── dns_issues.md
    └── loadbalancer_issues.md
```

In `catalog.json`, reference them with relative paths:
```json
{
  "catalog": [
    {
      "id": "postgres-db-issues",
      "update_date": "2025-11-27",
      "description": "Troubleshooting PostgreSQL database connection and performance issues",
      "link": "database/postgres_troubleshooting.md"
    }
  ]
}
```

## How It Works

1. Holmes loads your `catalog.json` file
2. The LLM compares runbook descriptions with user questions
3. When a match is found, Holmes fetches and follows the markdown runbook
4. The runbook guides the investigation process with step-by-step instructions
