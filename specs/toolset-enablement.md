# Toolset Enablement: How It Works Today and Why It's Confusing

## Problem Statement

The logic that determines whether a toolset is "enabled" at runtime is spread across
multiple files, uses different rules for different contexts (CLI vs server), and relies
on implicit conventions that are easy to get wrong. A developer reading the code cannot
answer the simple question "will this toolset be active?" without tracing through 4+
layers of logic.

---

## Recent Behavioral Change: `missing_config` Decoupled from `enabled`

PR #1830 removed the early-return guard in `Toolset.missing_config`:

```python
# BEFORE (old code):
@property
def missing_config(self) -> bool:
    if self.enabled or self.is_default:
        return False          # <-- short-circuited, never reported missing config
    ...

# AFTER (current code):
@property
def missing_config(self) -> bool:
    if not self.config_classes:
        return False
    requires_config = any(...)
    if not requires_config:
        return False
    return self.config is None  # <-- pure fact-check, ignores enabled state
```

**Why**: The old code conflated "is this toolset turned on?" with "does this toolset
have the config it needs?". A toolset with `enabled=True` but no required config
provided would report `missing_config=False`, hiding the problem. The auto-enable
logic in `ToolsetManager` (Layer 4 below) uses `missing_config` as a gate, so the
old behavior meant it could never protect against enabling an unconfigured toolset
that happened to already be enabled.

**Impact**: Toolsets with `enabled=True` in their constructor (bash, internet, etc.)
will now correctly report `missing_config=True` if they have required config fields
with no config provided. In practice this changes nothing today because none of these
always-on toolsets have required config fields. The change matters for future toolsets
and for the auto-enable path.

---

## How It Works Today

### Layer 1: The `Toolset.enabled` Default (tools.py:709)

Every toolset has `enabled: bool = False` in the base `Toolset` model. This means
**all toolsets start disabled** unless something explicitly flips the flag.

### Layer 2: Python Toolsets Can Override the Default (__init__.py)

Some Python toolsets pass `enabled=True` in their constructor. These are "always-on"
toolsets that don't require user configuration:

| Toolset | enabled= | Why |
|---|---|---|
| `bash` | `True` | Core capability, always available |
| `kubernetes/logs` | `True` | Core K8s capability |
| `internet` | `True` | Web search, always available |
| `connectivity_check` | `True` | Network checks, always available |
| `robusta` | `True if dal else False` | Conditional on having a DAL connection |
| `core_investigation` | `True` | Internal orchestration toolset |
| `runbook` | `True` | Runbook fetcher, always available |

All other Python toolsets (confluence, grafana, prometheus, datadog, elasticsearch,
etc.) keep `enabled=False` and rely on config or auto-enable to turn them on.

Note: `confluence` does NOT set `enabled=True` in its constructor despite appearances.
The `enabled=True` at line 225 of `confluence.py` is for an internal `HttpToolset`
instance, not the `ConfluenceToolset` itself. Confluence uses a `CallablePrerequisite`
(not env var prerequisites) and has required config fields (`api_url`, `api_key`), so
it is gated by the `missing_config` guard on the CLI auto-enable path.

### Layer 3: YAML Toolsets Never Set `enabled` (*.yaml files)

No YAML toolset file (kubernetes.yaml, helm.yaml, docker.yaml, etc.) contains an
`enabled:` field. They all inherit the base default of `False`.

### Layer 4: CLI vs Server — Two Completely Different Enablement Strategies

This is where the real confusion lives.

**CLI path** (`list_console_toolsets`):
```
enable_all_toolsets=True
```
The CLI calls `_list_all_toolsets(enable_all_toolsets=True)`, which loops over every
built-in toolset and sets `enabled=True` **unless** `missing_config` is True. This
means on the CLI, toolsets like `helm/core` and `kubernetes/core` are auto-enabled
even though they never set `enabled=True` themselves.

**Server path** (`list_server_toolsets`):
```
enable_all_toolsets=False
```
The server does NOT auto-enable anything. A toolset is only enabled if:
1. It set `enabled=True` in its Python constructor (Layer 2), OR
2. The user explicitly enabled it in `~/.holmes/config.yaml` under the `toolsets:` key

This means `helm/core` is available on CLI but **not** on the server unless the user
adds it to their config — even though nothing in the toolset definition or docs makes
this distinction obvious.

### Layer 5: Config Overrides (toolset_manager.py:230-260)

When a toolset appears in the user's config (`~/.holmes/config.yaml` under `toolsets:`):

- **If the toolset name matches a built-in**: it's treated as an override. The config
  values are merged onto the built-in toolset via `override_with()`. Crucially, just
  mentioning a built-in toolset in the config **does not** set `enabled=True` — the
  config must explicitly include `enabled: true`. However...

- **If the toolset name does NOT match a built-in**: it's treated as a custom toolset,
  and `enabled` **defaults to True** (line 256-259: `if toolset_config.get("enabled", True)
  is False`). So custom toolsets are opt-out, built-in toolsets are opt-in.

### Layer 6: Tag Filtering

Toolsets have tags (`core`, `cli`, `cluster`). Depending on the context:
- CLI: includes `core` + `cli` tags
- Server: includes `core` + `cluster` tags

A toolset tagged `cli` (e.g., `docker/core`, `aks/core`, cilium) is excluded from the
server path entirely, regardless of `enabled`.

### Layer 7: Prerequisites

Even after a toolset is "enabled", it must pass prerequisite checks (env vars present,
commands exist, callable checks pass). A toolset can be `enabled=True` but have
`status=FAILED` if prerequisites don't pass.

### Layer 8: `missing_config` Guard

The `enable_all_toolsets=True` path (CLI) skips toolsets where `missing_config` returns
True. This property checks whether the toolset's `config_classes` have required fields
(fields with no default) AND no `config` was provided.

**This guard actively fires in production.** Multiple toolsets have required config
fields declared as `str = Field(...)` with no default:

| Toolset | Required field(s) |
|---|---|
| `confluence` | `ConfluenceConfig.api_url`, `ConfluenceConfig.api_key` |
| `grafana/dashboards` | `GrafanaCommonConfig.api_url` |
| `elasticsearch/data`, `elasticsearch/cluster` | `ElasticsearchConfig.api_url` |
| `servicenow/tables` | `ServiceNowConfig.api_url` |
| `rabbitmq/core` | `RabbitMQConfig.api_url` |

When a user runs the CLI without configuring these toolsets, the auto-enable loop
calls `toolset.missing_config`, gets `True`, and skips them. Without this guard,
the CLI would attempt to enable every toolset and then fail at the prerequisite
check stage for all unconfigured ones — a much noisier and slower experience.

---

## The Confusing Parts

### 1. "Why is Helm available on CLI but not on server?"

Because the CLI uses `enable_all_toolsets=True` which auto-enables everything, while
the server requires explicit enablement. Nothing in the Helm toolset definition
(helm.yaml) or docs explains this. A user deploying HolmesGPT as a server will find
that Helm, Kubernetes core, ArgoCD, etc. are mysteriously missing.

### 2. "What does `enabled=False` actually mean?"

It depends on context:
- On CLI: it means almost nothing — `enable_all_toolsets=True` overrides it for any
  toolset where `missing_config` is False. Toolsets with required config fields but no
  config provided are the exception: they stay disabled.
- On server: it means the toolset is off unless config says otherwise

So the `enabled` field on a toolset definition is effectively only meaningful for the
server path (and for the `missing_config` edge case on CLI), which makes it misleading
for anyone reading the code.

### 3. "Built-in in config = enabled, right?"

Wrong. Mentioning a built-in toolset in config creates an override object, but the
override only sets fields that are explicitly provided. If you write:

```yaml
toolsets:
  helm/core:
    config:
      some_setting: value
```

...Helm is NOT enabled, because `enabled` wasn't set in the override, and the
`override_with()` method skips None/empty values. You need:

```yaml
toolsets:
  helm/core:
    enabled: true
```

But custom (non-built-in) toolsets default to enabled when mentioned in config.
This asymmetry is a common source of confusion.

### 4. "The enabled field, the status field, and prerequisites"

There are three related but distinct concepts:
- `enabled: bool` — whether the toolset should be considered at all
- `status: ToolsetStatusEnum` — DISABLED / ENABLED / FAILED (set after prereq checks)
- prerequisites — the actual checks that determine if the toolset can run

A toolset with `enabled=True` + `status=FAILED` is confusing: it's "enabled" but won't
be used. The `enabled` field is really "user wants this on" while `status` is "system
verified it works". These could be named better.

### 5. Python toolsets with `enabled=True` + prerequisites = always-try toolsets

Toolsets like `internet` and `connectivity_check` set `enabled=True` and have
prerequisites. On the server path, they're always enabled, always checked, and fail
if prerequisites aren't met. This is fine functionally but conceptually odd —
"enabled by default but expected to fail sometimes" is a weird pattern.

Note: `confluence` does NOT follow this pattern — it starts with `enabled=False` and
has required config fields, so it is gated by `missing_config` on the CLI path and
must be explicitly configured on the server path.

### 6. YAML toolsets and Python toolsets use different registration paths

YAML toolsets are loaded from `*.yaml` files in the toolsets directory. Python toolsets
are instantiated in `load_python_toolsets()`. Both end up in the same list, but the
loading paths are completely different and have different validation rules.

---

## What Would Simplify This

### Option A: Explicit Enablement Everywhere

Remove `enable_all_toolsets=True` from the CLI path. Instead, have each toolset
declare its own enablement strategy:

```python
class EnablementStrategy(Enum):
    ALWAYS = "always"           # enabled=True, no config needed (bash, internet)
    AUTO = "auto"               # enabled if prerequisites pass (helm, k8s)
    CONFIG_REQUIRED = "config"  # enabled only when user provides config (grafana, datadog)
```

The CLI and server paths would use the same logic. `AUTO` toolsets would be enabled
whenever their prerequisites pass, regardless of CLI vs server. `CONFIG_REQUIRED`
toolsets would need explicit user configuration in both contexts.

### Option B: Remove `enabled` from the Base Model

Instead of a boolean `enabled` flag plus a separate `status`, collapse them:

```python
class ToolsetState(Enum):
    NOT_CONFIGURED = "not_configured"  # needs user config
    CONFIGURED = "configured"          # config present, not yet checked
    AVAILABLE = "available"            # prerequisites passed
    UNAVAILABLE = "unavailable"        # prerequisites failed
```

This eliminates the confusing `enabled=True, status=FAILED` state.

### Option C: Unify CLI and Server Paths

The root cause of most confusion is that CLI and server have different enablement
logic. If there were a single `get_active_toolsets()` method with clear, documented
rules, the system would be much easier to reason about. The CLI's "enable everything"
behavior could be a tag-based default:

```yaml
# All core+cli toolsets auto-enable when prereqs pass
# All core+cluster toolsets require explicit config
```

This makes the CLI vs server distinction explicit in the toolset definitions rather
than buried in the manager code.

---

## Files Involved

| File | Role |
|---|---|
| `holmes/core/tools.py` (lines 704-766) | `Toolset` base model, `enabled=False` default, `missing_config` |
| `holmes/core/toolset_manager.py` | All enablement orchestration, CLI vs server split |
| `holmes/plugins/toolsets/__init__.py` | `load_builtin_toolsets`, `load_python_toolsets`, YAML loading |
| `holmes/plugins/toolsets/*.yaml` | YAML toolset definitions (never set `enabled`) |
| `holmes/plugins/toolsets/*/` | Python toolset classes (some set `enabled=True`) |
| `holmes/config.py` | User config loading, passes `toolsets` dict to manager |
