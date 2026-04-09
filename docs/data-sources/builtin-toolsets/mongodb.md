# MongoDB

Connect HolmesGPT to MongoDB databases to query data, investigate slow queries, analyze collection schemas, and diagnose performance issues like connection exhaustion, lock contention, and replication lag.

You can configure multiple MongoDB instances with different names (e.g., `prod-mongo`, `analytics-mongo`, `staging-mongo`).

## Creating a Read-Only User

```javascript
use admin
db.createUser({
  user: "holmes_readonly",
  pwd: "your_secure_password",
  roles: [
    { role: "readAnyDatabase", db: "admin" },
    { role: "clusterMonitor", db: "admin" }
  ]
})
```

The `readAnyDatabase` role grants read access to all databases. The `clusterMonitor` role enables the `serverStatus` and `currentOp` diagnostics tools.

## Configuration

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      prod-mongo:
        type: mongodb
        config:
          connection_url: "{{ env.PROD_MONGO_URL }}"
        llm_instructions: "Production MongoDB with customer and order data"

      analytics-mongo:
        type: mongodb
        config:
          connection_url: "{{ env.ANALYTICS_MONGO_URL }}"
        llm_instructions: "Analytics MongoDB for reporting"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    **Connection URL format:**

    ```
    mongodb://[username]:[password]@[host]:[port]/[database]
    mongodb+srv://[username]:[password]@[cluster].mongodb.net/[database]
    ```

=== "Holmes Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic mongodb-credentials \
      --from-literal=url='mongodb://holmes_readonly:your_secure_password@mongo.example.com:27017/mydb' \
      -n holmes
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    additionalEnvVars:
      - name: MONGO_URL
        valueFrom:
          secretKeyRef:
            name: mongodb-credentials
            key: url

    toolsets:
      prod-mongo:
        type: mongodb
        config:
          connection_url: "{{ env.MONGO_URL }}"
        llm_instructions: "Production MongoDB database"
    ```

    **Multiple instances:**

    ```yaml
    additionalEnvVars:
      - name: PROD_MONGO_URL
        valueFrom:
          secretKeyRef:
            name: mongo-prod
            key: url
      - name: ANALYTICS_MONGO_URL
        valueFrom:
          secretKeyRef:
            name: mongo-analytics
            key: url

    toolsets:
      prod-mongo:
        type: mongodb
        config:
          connection_url: "{{ env.PROD_MONGO_URL }}"

      analytics-mongo:
        type: mongodb
        config:
          connection_url: "{{ env.ANALYTICS_MONGO_URL }}"
    ```

=== "Robusta Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic mongodb-credentials \
      --from-literal=url='mongodb://holmes_readonly:your_secure_password@mongo.example.com:27017/mydb' \
      -n default
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: MONGO_URL
          valueFrom:
            secretKeyRef:
              name: mongodb-credentials
              key: url

      toolsets:
        prod-mongo:
          type: mongodb
          config:
            connection_url: "{{ env.MONGO_URL }}"
          llm_instructions: "Production MongoDB database"
    ```

    **Multiple instances:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: PROD_MONGO_URL
          valueFrom:
            secretKeyRef:
              name: mongo-prod
              key: url
        - name: ANALYTICS_MONGO_URL
          valueFrom:
            secretKeyRef:
              name: mongo-analytics
              key: url

      toolsets:
        prod-mongo:
          type: mongodb
          config:
            connection_url: "{{ env.PROD_MONGO_URL }}"

        analytics-mongo:
          type: mongodb
          config:
            connection_url: "{{ env.ANALYTICS_MONGO_URL }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Configuration Options

- **connection_url** (required): MongoDB connection string (`mongodb://` or `mongodb+srv://`)
- **default_database**: Database to use when not specified per-query (also read from URL)
- **read_only** (default: `true`): Only allow read operations (find, aggregate without `$out`/`$merge`)
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum documents to return per query (1-10000)
- **timeout_seconds** (default: `30`): Connection and operation timeout
- **llm_instructions**: Context about this database for the LLM

## Tools

| Tool | Description |
|------|-------------|
| `{prefix}_find` | Query documents with filter, projection, sort, and limit |
| `{prefix}_aggregate` | Run aggregation pipelines ($match, $group, $sort, $lookup, etc.) |
| `{prefix}_list_databases` | List all databases with their sizes |
| `{prefix}_list_collections` | List collections in a database |
| `{prefix}_collection_schema` | Infer field types from sample documents and show indexes |
| `{prefix}_server_status` | Server diagnostics: connections, memory, opcounters, locks, WiredTiger cache |
| `{prefix}_current_op` | Find slow or blocked operations, filterable by duration |

`{prefix}` is derived from the instance name (e.g., `prod_mongo_find` for `prod-mongo`).

## Common Use Cases

```
"List all collections in the orders database and show their schemas"
```

```
"Find the 10 most recent error documents in the events collection"
```

```
"How many orders per status are there in the last 24 hours?"
```

```
"Are there any slow queries running on the MongoDB server?"
```

```
"Check the MongoDB server health — connections, memory, and lock statistics"
```
