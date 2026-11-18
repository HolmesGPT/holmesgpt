# Redis (MCP)

The Redis MCP server lets Holmes inspect a Redis database: keys, lists, hashes, sets, sorted sets, streams, and server statistics. It uses the official [`redis/mcp-redis`](https://github.com/redis/mcp-redis) server.

!!! warning "The server can change data"
    `redis-mcp-server` exposes 53 tools, including `set`, `delete`, `lpop`, `xdel` and `publish`. The configuration below uses [`allowed_tools`](../remote-mcp-servers.md) to load only the read-only tools. Also connect with a Redis ACL user that has read-only permissions. The `+@read` category does not include `INFO` or `CLIENT LIST`, so with only `+@read` the `info` and `client_list` tools return a permission error. Also grant `+info` and `+client|list`.

## Prerequisites

- A Redis instance that Holmes can reach, and its connection URL without the password, for example `redis://localhost:6379/0`, or `rediss://user@host:6379/0` for TLS.
- [`uv`](https://docs.astral.sh/uv/), which provides `uvx`, to run the server. The server needs Python 3.10 or newer.

## Configuration

The server only supports the `stdio` transport, which the CLI runs as a subprocess.

The server reads the password from the `REDIS_PWD` environment variable. Keep the password out of `--url`: a password in the URL is visible in the process list of the machine that runs Holmes.

```bash
export REDIS_PWD="<REDIS_PASSWORD>"
```

Holmes does not load the toolset if an `{{ env.NAME }}` it references is not set. For a Redis without a password, delete the `env` block below.

Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

```yaml
mcp_servers:
  redis:
    description: "Redis (read-only inspection via redis/mcp-redis)"
    config:
      mode: stdio
      command: "uvx"
      args: ["redis-mcp-server@0.5.1", "--url", "redis://localhost:6379/0"]
      env:
        REDIS_PWD: "{{ env.REDIS_PWD }}"
    # Load only read-only tools. The server also exposes tools that write or delete data.
    allowed_tools:
      - get
      - hget
      - hgetall
      - hexists
      - lrange
      - llen
      - smembers
      - zrange
      - xrange
      - scan_keys
      - scan_all_keys
      - type
      - dbsize
      - info
      - client_list
    llm_instructions: "Use Redis to inspect keys, queue lengths, streams and server stats. This connection is read-only. Use `type` first when you are not sure what kind of value a key holds."
```

--8<-- "snippets/toolset_refresh_warning.md"

### Kubernetes

The server has no HTTP transport, so in a cluster run it in its own pod behind [Supergateway](https://github.com/supercorp-ai/supergateway) and point Holmes at the pod's URL. The [Stdio](../remote-mcp-servers.md#stdio) section of the MCP servers page shows the pod and the Helm values. Install `redis-mcp-server` in the image and pass the whole command, with the Redis URL, as the one `--stdio` string: `"redis-mcp-server --url redis://<REDIS_HOST>:6379/0"`. Without `--url` the server connects to `127.0.0.1:6379` inside its own pod. Keep the password out of that command as well: set `REDIS_PWD` on the pod from a Secret, the way the manifest's `env` block sets `API_KEY`. Use the same `allowed_tools` list.

Supergateway does not authenticate its clients, and `allowed_tools` only filters the tools Holmes loads. Any pod that can reach the Service can call every tool of the server with the permissions of the Redis user. Keep the Service `ClusterIP` and allow ingress to the pod only from the Holmes pods with a NetworkPolicy (it needs a network plugin that enforces NetworkPolicies):

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: redis-mcp-allow-holmes
spec:
  podSelector:
    matchLabels:
      app: redis-mcp   # the label of your Supergateway pod
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: holmes
      ports:
        - port: 8000
```

Apply it in the namespace of the Supergateway pod. Run that pod in the namespace of the Holmes pods (`app: holmes` is the label the Helm chart gives them), as the NetworkPolicies of the chart's bundled MCP servers assume.

## Available Tools

With the configuration above Holmes loads these tools:

| Tool | Description |
|------|-------------|
| `get` | Get a string value |
| `hget`, `hgetall`, `hexists` | Read fields of a hash |
| `lrange`, `llen` | Read a list or get its length |
| `smembers` | List the members of a set |
| `zrange` | Read a range of a sorted set |
| `xrange` | Read entries of a stream |
| `scan_keys`, `scan_all_keys` | Find keys matching a pattern |
| `type` | Get the type of a key |
| `dbsize` | Count the keys in the database |
| `info` | Redis server information and statistics |
| `client_list` | List connected clients |

The server offers more tools (JSON, pub/sub, vector search, consumer groups, writes). Add them to `allowed_tools` only if you want Holmes to use them.

Name the keys or key patterns in your question when you know them. Without them, Holmes may guess key names that do not exist.

## Testing the Connection

```bash
holmes ask "How many entries are in the Redis list queue:calls, and what are the first and last entries?"
```

## Common Use Cases

```bash
holmes ask "How many calls are waiting in the queue:calls list in Redis?"
```

```bash
holmes ask "Show the fields of the Redis hash session:s-1002 and tell me why it is stuck"
```

```bash
holmes ask "Check the Redis keys queue:calls and config:max_workers. Is the queue backed up, and what could explain it?"
```

## Limitations

- Redis Streams: the read-only tools can read entries with `xrange`, but not the pending entries of a consumer group (`XPENDING`) or `XINFO`, so Holmes cannot tell whether a group is falling behind.

## Additional Resources

- [redis/mcp-redis](https://github.com/redis/mcp-redis)
- [Remote MCP servers](../remote-mcp-servers.md)
