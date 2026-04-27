# Skills

!!! warning "Breaking Change — Holmes 0.25.0+"

    Skills replace the previous runbook system. If you are upgrading from Holmes 0.24.x or older, you must migrate your runbooks to the new SKILL.md format. See [Migrating from Runbooks](#migrating-from-runbooks) below.

Skills are step-by-step troubleshooting guides that Holmes follows when investigating issues. When a user asks a question or an alert fires, Holmes automatically matches relevant skills from its catalog and fetches them using the `fetch_skill` tool. It then follows the skill instructions step-by-step, calling tools to gather data and reporting results for each step.

Skills work with all Holmes interfaces — the CLI (`ask` and `investigate` commands), the HTTP server, and the Python SDK.

## How It Works

1. Holmes receives a question or alert
2. Holmes compares the issue against skill descriptions in the catalog
3. If a skill matches, Holmes fetches it with the `fetch_skill` tool
4. Holmes follows the skill steps, calling tools to gather data at each step
5. Holmes reports findings with a checklist showing completed and skipped steps

## Built-in Skills

Holmes ships with built-in skills at `holmes/plugins/skills/builtin/`. These are available automatically — no configuration needed.

## Custom Skills

You can add your own skills by creating SKILL.md files and pointing Holmes to them.

### Skill Format

Each skill is a directory containing a `SKILL.md` file with YAML frontmatter and a markdown body:

```
my-skills/
├── dns-troubleshooting/
│   └── SKILL.md
├── postgres-performance/
│   └── SKILL.md
└── redis-connection-issues/
    └── SKILL.md
```

**`dns-troubleshooting/SKILL.md`:**

```markdown
---
name: dns-troubleshooting
description: Troubleshooting DNS resolution failures in Kubernetes clusters
---

# DNS Troubleshooting

## Goal
Diagnose and resolve DNS resolution issues in the cluster.
Follow the workflow steps sequentially.

## Workflow

1. **Check CoreDNS pods**
   * Verify pods in kube-system with label k8s-app=kube-dns are running
   * Check for restarts or resource pressure

2. **Test DNS resolution**
   * Resolve kubernetes.default.svc.cluster.local from an affected pod
   * Resolve an external domain like google.com

3. **Check for NetworkPolicies blocking DNS**
   * List NetworkPolicies in the affected namespace
   * Verify UDP port 53 egress to kube-system is allowed

## Synthesize Findings
Correlate the outputs from each step to identify the root cause.

## Recommended Remediation Steps
* **CoreDNS down**: Check resource limits and node capacity
* **NetworkPolicy blocking**: Add an egress rule allowing DNS traffic
* **ConfigMap wrong**: Fix the Corefile and restart CoreDNS
```

### Frontmatter Fields

- **`name`** (optional): Lowercase with hyphens. Defaults to the parent directory name.
- **`description`** (required): Used by the LLM to match the skill to user questions — make this descriptive.

### Writing a Skill

The key sections in a skill's markdown body are:

- **Goal**: What the skill addresses
- **Workflow**: Sequential diagnostic steps Holmes will execute using its tools
- **Synthesize Findings**: How to interpret combined results
- **Recommended Remediation Steps**: Solutions based on findings

### Configuring Custom Skill Paths

=== "Config File"

    Add skill directory paths to `~/.holmes/config.yaml`:

    ```yaml
    custom_skill_paths:
      - /path/to/my-skills/
      - /path/to/team-skills/
    ```

=== "Helm Chart"

    Mount your skill directories and reference them in values:

    ```yaml
    custom_skill_paths:
      - /etc/holmes/skills/
    ```

=== "Python SDK"

    ```python
    from pathlib import Path

    from holmes.config import Config

    config = Config.load_from_file(
        config_file=Path("~/.holmes/config.yaml").expanduser(),
    )
    # custom_skill_paths is read from the config file
    catalog = config.get_skill_catalog()
    ```

Holmes scans each directory (up to 2 levels deep) for `SKILL.md` files. Multiple paths are merged — skills from all paths are combined with built-in skills.

## Common Use Cases

```
Why is my PostgreSQL database connection timing out?
```

```
Investigate the OOMKilled alert on the payments service
```

```
Help me troubleshoot DNS resolution failures in the staging cluster
```

## Migrating from Runbooks

If you are upgrading from Holmes 0.24.x or older, your existing runbooks need to be converted to the SKILL.md format.

**For each runbook in your catalog:**

1. Create a directory named after the runbook (lowercase, hyphens):
   ```
   my-skills/postgres-troubleshooting/
   ```

2. Create a `SKILL.md` file inside it with the description from your old `catalog.json` entry as frontmatter, and the original markdown content as the body:
   ```markdown
   ---
   name: postgres-troubleshooting
   description: Troubleshooting PostgreSQL connection and performance issues
   ---

   (paste your original .md runbook content here)
   ```

3. Replace `custom_runbook_catalogs` in your config with `custom_skill_paths`:
   ```yaml
   # Old (no longer supported):
   # custom_runbook_catalogs:
   #   - /path/to/catalog.json

   # New:
   custom_skill_paths:
     - /path/to/my-skills/
   ```

The `catalog.json` file is no longer needed — Holmes discovers skills automatically by scanning for `SKILL.md` files.

## Troubleshooting

```bash
# Check Holmes logs for skill loading errors
# Look for "Failed to parse" or "missing required 'description' field"
holmes ask "test question" -v

# Verify your SKILL.md has valid YAML frontmatter
python3 -c "
import yaml
with open('my-skill/SKILL.md') as f:
    content = f.read()
    parts = content.split('---', 2)
    print(yaml.safe_load(parts[1]))
"
```
