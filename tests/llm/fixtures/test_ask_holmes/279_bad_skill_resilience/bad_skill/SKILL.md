---
name: app-279-error-log-querying
description: 'Any Elasticsearch error-log query against app-279-logs-* — these indices use ``lvl`` (keyword) for log severity, not ``severity`` or ``level``.'
---

## When to use

Any Elasticsearch ERROR / WARN / INFO count or filter query against the
`app-279-logs-*` indices in this cluster.

## Failed call shape (avoid)

```json
GET app-279-logs-*/_search
{ "query": { "term": { "level": "ERROR" } } }
```

Returns zero hits because the field is named `lvl`, not `level`.

## Working call shape

```json
GET app-279-logs-*/_search
{ "query": { "term": { "lvl": "ERR" } } }
```

This index normalizes severity to the short codes `ERR`, `WRN`, `INF`,
`DBG` — full words like `"ERROR"` will also miss even with the right
field name.

## Why this is env-specific

This team's logging library uses a custom schema with shortened field
name (`lvl`) and three-letter severity codes (`ERR`, `WRN`, `INF`,
`DBG`) — not documented in any public ES schema and a fresh LLM would
default to `level: "ERROR"`.
