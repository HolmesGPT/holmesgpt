# Redis (MCP)

The Redis MCP server lets Holmes inspect a Redis database: keys, lists, hashes, sets, sorted sets, streams, and server statistics. It uses the official [`redis/mcp-redis`](https://github.com/redis/mcp-redis) server.

!!! warning "The server can change data"
    `redis-mcp-server` exposes 53 tools, including `set`, `delete`, `lpop`, `xdel` and `publish`. The configuration below uses [`allowed_tools`](../remote-mcp-servers.md) to load only the read-only tools. Also connect with a Redis ACL user that has read-only permissions.

## Prerequisites

- A Redis instance that Holmes can reach, and its connection URL, for example `redis://localhost:6379/0` or `rediss://user:password@host:6379/0` for TLS.
- [`uv`](https://docs.astral.sh/uv/), which provides `uvx`, to run the server. The server needs Python 3.10 or newer.

## Configuration

The server only supports the `stdio` transport, which the CLI runs as a subprocess.

Set the connection URL as an environment variable if it contains a password:

```bash
export REDIS_URL="redis://localhost:6379/0"
```

Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

```yaml
mcp_servers:
  redis:
    description: "Redis (read-only inspection via redis/mcp-redis)"
    config:
      mode: stdio
      command: "uvx"
      args: ["redis-mcp-server@0.5.1", "--url", "{{ env.REDIS_URL }}"]
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

The server has no HTTP transport, so in a cluster run it in its own pod behind [Supergateway](https://github.com/supercorp-ai/supergateway) and point Holmes at the pod's URL. The [Stdio](../remote-mcp-servers.md#stdio) section of the MCP servers page shows the pod and the Helm values; use `redis-mcp-server` as the command and the same `allowed_tools` list.

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

Name the keys or key patterns in your question when you know them. Without them, Holmes may guess key names that do not exist.

## Limitations

- Redis Streams: the read-only tools can read entries with `xrange`, but not the pending entries of a consumer group (`XPENDING`) or `XINFO`, so Holmes cannot tell whether a group is falling behind.

## Additional Resources

- [redis/mcp-redis](https://github.com/redis/mcp-redis)
- [Remote MCP servers](../remote-mcp-servers.md)
