---
name: quirks-for-querying-elasticsearch
description: 'Known schema and query quirks for this team''s elasticsearch in this environment — fetch BEFORE issuing the first query against elasticsearch to skip wrong-call recovery.'
---
# Known quirks for querying elasticsearch

This skill collects every env-specific correction this team's investigations have discovered for elasticsearch. Each entry lists the wrong call shape (the default a fresh agent would try), the working shape, and why the correction is non-obvious. Scan the entries below and use the relevant one when you issue your query.

---

## 1. Index app-269-logs-a-* uses `severity` keyword, not `level`

**When to use:** Any Elasticsearch query targeting log level in the index app-269-logs-a-* — this team's app-A schema does not use the standard `level` field

**Failed call shape (avoid):**

elasticsearch_search with query `{"term": {"level": "ERROR"}}` on index `app-269-logs-a-*` — returns 0 hits because the field `level` does not exist in this index's mapping

**Working call shape:**

elasticsearch_search with query `{"term": {"severity": "ERROR"}}` on index `app-269-logs-a-*` — this index stores log severity in a keyword field named `severity`

**Why this is env-specific:**

This team's app-A elasticsearch indices use a custom logging schema where log severity is stored in a field named `severity` instead of the conventional `level` field. A fresh LLM would default to `level` and get zero results.
