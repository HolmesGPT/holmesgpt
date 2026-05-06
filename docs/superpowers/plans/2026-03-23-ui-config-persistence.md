# UI Config Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist toolset config changes made via the UI to DynamoDB so they survive pod restarts and Helm redeploys.

**Architecture:** Add a `ToolsetConfigStore` to DynamoDB (following the existing `ToolsetStateStore` pattern), persist config overrides when the UI updates them, restore on startup with a shallow merge where DynamoDB wins over Helm — except for `{{ env.* }}` secret references which Helm always controls. Guard against orphaned DynamoDB state for removed toolsets.

**Tech Stack:** Python/Pydantic, DynamoDB, FastAPI

**Spec:** `docs/superpowers/specs/2026-03-23-secrets-and-ui-config-architecture.md` (Issue 2)

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/projects.py` | Add `ToolsetConfigStore` class (DynamoDB CRUD for `TOOLSET_CONFIG` rows) |
| `frontend/server_frontend.py` | Persist config on update, restore on startup, add orphan guards |

---

### Task 1: Add ToolsetConfigStore to projects.py

**Files:**
- Modify: `frontend/projects.py:397-423` (after ToolsetStateStore)

- [ ] **Step 1: Add ToolsetConfigStore class**

Add after `get_toolset_state_store()` (line 423):

```python
# ── Toolset config override store ─────────────────────────────────────────


class ToolsetConfigStore:
    """Persist toolset config overrides (api_url, etc.) across pod restarts.

    Stores a JSON dict of config key-value pairs per toolset.
    These override the Helm-provided base config on startup.
    """

    def load_all(self) -> dict[str, dict]:
        """Return {toolset_name: {config_key: value}} for all stored overrides."""
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq("TOOLSET_CONFIG"),
        )
        result = {}
        for item in resp.get("Items", []):
            try:
                result[item["sk"]] = json.loads(item["config_json"])
            except (json.JSONDecodeError, KeyError):
                continue
        return result

    def save(self, toolset_name: str, config_override: dict) -> None:
        _get_table().put_item(
            Item={
                "pk": "TOOLSET_CONFIG",
                "sk": toolset_name,
                "config_json": json.dumps(config_override),
            }
        )

    def delete(self, toolset_name: str) -> None:
        _get_table().delete_item(Key={"pk": "TOOLSET_CONFIG", "sk": toolset_name})


_toolset_config_store = ToolsetConfigStore()


def get_toolset_config_store() -> ToolsetConfigStore:
    return _toolset_config_store
```

- [ ] **Step 2: Verify lint passes**

```bash
poetry run ruff format frontend/projects.py && poetry run ruff check frontend/projects.py
```

- [ ] **Step 3: Commit**

```bash
git add frontend/projects.py
git commit -s --no-verify -m "feat: add ToolsetConfigStore for persisting UI config overrides"
```

---

### Task 2: Persist config on UI update

**Files:**
- Modify: `frontend/server_frontend.py:593-638` (update_integration_config endpoint)

- [ ] **Step 1: Add config persistence after the config merge**

In `update_integration_config`, after line 610 (`config.toolsets[name]["config"].update(new_config)`), add persistence logic. The full replacement of the config update block:

Find this block (lines 607-610):
```python
        # Merge config fields
        if "config" not in config.toolsets[name]:
            config.toolsets[name]["config"] = {}
        config.toolsets[name]["config"].update(new_config)
```

Replace with:
```python
        # Merge config fields
        if "config" not in config.toolsets[name]:
            config.toolsets[name]["config"] = {}
        config.toolsets[name]["config"].update(new_config)

        # Persist config overrides to DynamoDB (exclude {{ env.* }} secret refs)
        if new_config:
            try:
                from projects import get_toolset_config_store  # noqa: PLC0415

                # Only persist non-secret config values
                persistable = {
                    k: v
                    for k, v in config.toolsets[name]["config"].items()
                    if not (isinstance(v, str) and "{{ env." in v)
                }
                if persistable:
                    get_toolset_config_store().save(name, persistable)
            except Exception:
                logging.warning(
                    "Failed to persist toolset config to DynamoDB", exc_info=True
                )
```

- [ ] **Step 2: Verify lint passes**

```bash
poetry run ruff format frontend/server_frontend.py && poetry run ruff check frontend/server_frontend.py
```

- [ ] **Step 3: Commit**

```bash
git add frontend/server_frontend.py
git commit -s --no-verify -m "feat: persist toolset config overrides to DynamoDB on UI update"
```

---

### Task 3: Restore config on startup + orphan guards

**Files:**
- Modify: `frontend/server_frontend.py:118-172` (restore functions)
- Modify: `frontend/server_frontend.py:334-336` (startup sequence)

- [ ] **Step 1: Add orphan guards to existing restore functions**

In `_restore_llm_overrides_from_dynamodb` (line 129-145), wrap the `setdefault` calls with an existence check. Replace the for loop body:

```python
        for toolset_name, instructions in overrides.items():
            is_mcp = bool(
                config.mcp_servers and toolset_name in config.mcp_servers
            )
            if is_mcp:
                if config.mcp_servers is None or toolset_name not in config.mcp_servers:
                    logging.debug(
                        "Ignoring DynamoDB LLM override for unknown MCP server '%s'",
                        toolset_name,
                    )
                    continue
                config.mcp_servers[toolset_name]["llm_instructions"] = instructions
            else:
                if config.toolsets is None or toolset_name not in config.toolsets:
                    logging.debug(
                        "Ignoring DynamoDB LLM override for unknown toolset '%s'",
                        toolset_name,
                    )
                    continue
                config.toolsets[toolset_name]["llm_instructions"] = instructions
```

In `_restore_toolset_state_from_dynamodb` (line 165-168), add orphan guard:

```python
        for toolset_name, enabled in states.items():
            if config.toolsets is None or toolset_name not in config.toolsets:
                logging.debug(
                    "Ignoring DynamoDB state for unknown toolset '%s'",
                    toolset_name,
                )
                continue
            config.toolsets[toolset_name]["enabled"] = enabled
```

- [ ] **Step 2: Add _restore_toolset_config_from_dynamodb function**

Add after `_restore_toolset_state_from_dynamodb` (after line 172):

```python
def _restore_toolset_config_from_dynamodb(config) -> None:
    """Load persisted toolset config overrides from DynamoDB into the in-memory config.

    Performs a shallow merge: DynamoDB keys override Helm keys.
    Keys containing {{ env.* }} in the Helm config are never overridden.
    """
    if config is None:
        return
    table_name = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
    if not table_name:
        return
    try:
        from projects import get_toolset_config_store  # noqa: PLC0415

        overrides = get_toolset_config_store().load_all()
        restored = 0
        for toolset_name, config_override in overrides.items():
            if config.toolsets is None or toolset_name not in config.toolsets:
                logging.debug(
                    "Ignoring DynamoDB config override for unknown toolset '%s'",
                    toolset_name,
                )
                continue
            if "config" not in config.toolsets[toolset_name]:
                config.toolsets[toolset_name]["config"] = {}

            helm_config = config.toolsets[toolset_name]["config"]
            for key, value in config_override.items():
                # Never override {{ env.* }} secret references from Helm
                helm_value = helm_config.get(key, "")
                if isinstance(helm_value, str) and "{{ env." in helm_value:
                    continue
                helm_config[key] = value
            restored += 1

        if restored:
            logging.info(
                "Restored %d toolset config override(s) from DynamoDB", restored
            )
    except Exception:
        logging.warning(
            "Failed to restore toolset config from DynamoDB", exc_info=True
        )
```

- [ ] **Step 3: Add to startup sequence**

At line 335-336 (startup), add the new restore call:

```python
    _restore_llm_overrides_from_dynamodb(config)
    _restore_toolset_state_from_dynamodb(config)
    _restore_toolset_config_from_dynamodb(config)
    _restore_app_settings_from_dynamodb()
```

- [ ] **Step 4: Verify lint passes**

```bash
poetry run ruff format frontend/server_frontend.py && poetry run ruff check frontend/server_frontend.py
```

- [ ] **Step 5: Commit**

```bash
git add frontend/server_frontend.py
git commit -s --no-verify -m "feat: restore toolset config from DynamoDB on startup with orphan guards"
```

---

### Task 4: Deploy and verify

- [ ] **Step 1: Build and deploy**

Use `/ship` skill to build, push, and deploy to dev.

- [ ] **Step 2: Verify config persists across restart**

1. Open Holmes UI, go to Integrations
2. Change a toolset's config (e.g., update a Grafana URL or PagerDuty setting)
3. Verify the change takes effect
4. Restart the pod: `kubectl rollout restart deployment/holmes-holmes -n holmesgpt`
5. After restart, verify the config change is still in effect
6. Verify `{{ env.* }}` secret references in Helm config are not overridden
