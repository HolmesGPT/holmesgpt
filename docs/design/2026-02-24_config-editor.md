# Toolset Config Editor

## Intent

HolmesGPT toolsets (Prometheus, Grafana, Elasticsearch, etc.) each have their own configuration — URLs, API keys, query defaults, and so on.
Before this editor existed, users had to hand-edit `~/.holmes/config.yaml`, cross-referencing the Pydantic schema of each toolset to know which fields were available and what types they expected.
The config editor gives users a terminal UI that discovers configurable toolsets automatically, presents their schema as an editable tree, and writes valid YAML back to the config file.

## Entry Points

The editor can be reached two ways:

- **CLI** — `holmes toolset config` launches the editor as a standalone command.
- **Interactive mode** — the `/config` slash command opens the editor mid-session. When a save occurs, the toolset is refreshed in-place so the new configuration takes effect immediately without restarting.

## UX Flow

The editor is a two-screen flow that runs entirely in the terminal:

### Screen 1 — Toolset Selection

An arrow-key menu listing every toolset that exposes a Pydantic config class.
Each entry shows the toolset name, its current status (enabled / disabled / failed), and whether it already has saved configuration.
The user picks one and presses Enter.

### Screen 2 — Tree Editor

The main editing surface. If the toolset already has saved configuration, those values are loaded automatically; otherwise the editor starts with schema defaults.

The toolset's Pydantic schema is walked at startup and rendered as a tree of fields:

- **Primitive fields** (str, int, float, bool) are edited inline.
- **Nested models** appear as collapsible sections with their own children.
- **Dicts and lists** support adding and removing entries with hotkeys.
- **Bool fields** toggle on Enter rather than opening a text buffer.

Four action buttons sit below the tree:

| Button   | Behaviour |
|----------|-----------|
| Test     | Deep-copies the toolset, applies the current values, and runs prerequisite checks. Output is captured and displayed inline. |
| Reset    | Discards all current values and rebuilds the tree from schema defaults, including lists and dicts. |
| Save     | Merges the toolset config into the config file on disk, creating the file and parent directories if needed. |
| Exit     | Returns to the caller. If a save occurred during the session, the toolset is refreshed (config re-applied, prerequisites re-checked) so it becomes usable immediately. |

## Structure

Everything lives in a single module: `holmes/toolset_config_tui.py`.

Internally the code is organised into a few layers:

- **Type introspection helpers** — walk Pydantic `model_fields` to resolve annotations into simple type tags (`str`, `int`, `dict`, `model`, etc.).
- **`ConfigFieldNode` dataclass** — the tree node that the editor operates on. Each node knows its key, type, current value, depth, parent, and children.
- **`build_tree_from_schema` / `tree_to_dict`** — convert between the Pydantic schema (plus current values) and the `ConfigFieldNode` tree, and back to a plain dict for serialisation.
- **Screen functions** (`select_toolset`, `run_tree_editor`) — each screen is a self-contained prompt_toolkit `Application` with its own key bindings and layout.
- **`run_toolset_config_tui`** — the orchestrator that sequences the two screens and handles the post-save refresh.

## Libraries

| Library | Role |
|---------|------|
| **prompt_toolkit** | Drives both screens — key bindings, cursor management, the `Application` run-loop, and `FormattedTextControl` for rendering. Chosen because it works in any terminal, supports raw keyboard input, and is already a HolmesGPT dependency (used by the interactive REPL). |
| **Rich** | Used only at the edges: printing status messages and panels before/after the prompt_toolkit screens. |
| **Pydantic** | The source of truth for each toolset's schema. The editor reads `model_fields`, `annotation`, defaults, and descriptions to build the tree. |
| **PyYAML** | Reads and writes `~/.holmes/config.yaml`. The save path does a careful merge so it preserves unrelated config sections. |
