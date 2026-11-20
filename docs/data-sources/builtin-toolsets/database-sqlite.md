# SQLite

Connect HolmesGPT to SQLite databases to analyze query performance, examine table schemas, check index usage, and read data for troubleshooting local databases.

You can configure multiple SQLite instances with different names (e.g., `dev-sqlite`, `test-sqlite`, `app-cache-sqlite`).

## File Permissions

Ensure the database file is accessible:

```bash
# Make database readable
chmod 644 /path/to/database.db

# If Holmes runs as specific user, ensure ownership
chown holmes:holmes /path/to/database.db

# Or use group access
chmod 664 /path/to/database.db
chgrp holmes-group /path/to/database.db
```

## Configuration

**Connection URL format:**

```
sqlite:///[absolute_path_to_file]
```

**Important:** Use four slashes (`////`) for absolute paths:

- `sqlite:////home/user/app.db` - Absolute path on Linux/Mac
- `sqlite:////var/lib/app/data.db` - Another Linux example

In Kubernetes, the database file is read from a mounted volume. Each Kubernetes example on this page mounts it with a `hostPath` volume of `type: File`, which requires the file to exist at that path on the node the Holmes pod is scheduled on; where it does not, the volume fails to mount and the pod does not start.

```yaml-toolset-config
additionalVolumes:
  - name: sqlite-db
    hostPath:
      path: /path/on/host/database.db
      type: File

additionalVolumeMounts:
  - name: sqlite-db
    mountPath: /data/database.db
    readOnly: true

toolsets:
  app-sqlite:
    type: database
    config:
      connection_url: "sqlite:////data/database.db"
    llm_instructions: "Application database mounted from host"
---
cli: |
  **~/.holmes/config.yaml:**

  ```yaml
  toolsets:
    dev-sqlite:
      type: database
      config:
        connection_url: "sqlite:////absolute/path/to/database.db"
      llm_instructions: "Local development database with test data"

    app-cache-sqlite:
      type: database
      config:
        connection_url: "sqlite:////var/lib/app/cache.db"
      llm_instructions: "Application cache database"
  ```

  **In-memory database (testing only):**

  ```yaml
  connection_url: "sqlite:///:memory:"
  ```
```

### Multiple instances

```yaml-toolset-config
additionalVolumes:
  - name: app-db
    hostPath:
      path: /data/app.db
      type: File
  - name: cache-db
    hostPath:
      path: /data/cache.db
      type: File

additionalVolumeMounts:
  - name: app-db
    mountPath: /data/app.db
    readOnly: true
  - name: cache-db
    mountPath: /data/cache.db
    readOnly: true

toolsets:
  app-sqlite:
    type: database
    config:
      connection_url: "sqlite:////data/app.db"

  cache-sqlite:
    type: database
    config:
      connection_url: "sqlite:////data/cache.db"
---
cli: |
  Add the following to **~/.holmes/config.yaml**, with the paths of the database files on your machine. Create the file if it doesn't exist:

  ```yaml
  toolsets:
    app-sqlite:
      type: database
      config:
        connection_url: "sqlite:////absolute/path/to/app.db"

    cache-sqlite:
      type: database
      config:
        connection_url: "sqlite:////absolute/path/to/cache.db"
  ```

  --8<-- "snippets/toolset_refresh_warning.md"
```

## Configuration Options

- **connection_url** (required): SQLite connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **max_rows** (default: `200`): Maximum rows to return (1-10000)
- **llm_instructions**: Context about this database

## Common Use Cases

```
"Show all tables and their row counts"
```

```
"Analyze query: SELECT * FROM users WHERE email LIKE '%@example.com'"
```

```
"Suggest indexes for the orders table"
```
