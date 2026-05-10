# Skills

!!! note "Requires Holmes 0.26.0+"

    Skills are supported starting in Holmes 0.26.0. Earlier versions use the legacy runbook system.

!!! warning "Breaking Change — Holmes 0.26.0+"

    Skills replace the previous runbook system. If you are upgrading from Holmes 0.25.x or older, you must migrate your runbooks to the new SKILL.md format. See [Migrating from Runbooks](#migrating-from-runbooks) below.

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

=== "Helm"

    Two modes are supported, and they can be combined.

    **Mode 1 — paths only (you mount skill files yourself):**

    Use this when you want to manage skill content outside Helm — e.g.
    keep skills in a Secret (good for content you don't want in plain
    values), pull them at start-up via an `initContainer`, or mount them
    from a pre-existing ConfigMap. You ship the files; the chart only
    registers the path.

    Holmes expects each skill to live in its own directory containing a
    `SKILL.md` file:

    ```
    /etc/holmes/my-skills/
    ├── dns-troubleshooting/SKILL.md
    └── pod-restart-quickcheck/SKILL.md
    ```

    Kubernetes ConfigMap and Secret keys cannot contain `/`, so use the
    `items:` projection on the volume to map flat keys (e.g.
    `dns-troubleshooting.SKILL.md`) to the directory layout above.

    **Example with a Secret:**

    Create the Secret:

    ```yaml
    apiVersion: v1
    kind: Secret
    metadata:
      name: holmes-custom-skills
      namespace: <holmes-namespace>
    type: Opaque
    stringData:
      dns-troubleshooting.SKILL.md: |
        ---
        description: Troubleshoot DNS resolution failures
        ---

        ## Goal
        Diagnose DNS issues in the cluster.

        ## Workflow
        1. Check CoreDNS pods
        2. Test DNS resolution from an affected pod
        3. Check NetworkPolicies for blocked egress to kube-system
      pod-restart-quickcheck.SKILL.md: |
        ---
        description: Quick diagnosis for CrashLoopBackOff / restarting pods
        ---

        ## Goal
        Identify why a pod is restarting.

        ## Workflow
        1. Inspect pod status (restartCount, lastState.terminated.reason)
        2. Pull `--previous` container logs
        3. Check namespace events
    ```

    Wire it into the Holmes pod through the chart's existing
    `additionalVolumes` / `additionalVolumeMounts` knobs:

    ```yaml
    additionalVolumes:
      - name: custom-skills
        secret:
          secretName: holmes-custom-skills
          items:
            - key: dns-troubleshooting.SKILL.md
              path: dns-troubleshooting/SKILL.md
            - key: pod-restart-quickcheck.SKILL.md
              path: pod-restart-quickcheck/SKILL.md
    additionalVolumeMounts:
      - name: custom-skills
        mountPath: /etc/holmes/my-skills
        readOnly: true
    customSkillPaths:
      - /etc/holmes/my-skills
    ```

    Swap `secret:` for `configMap:` (with the same `items:` projection)
    if you'd rather use a ConfigMap. Updates to the underlying
    Secret/ConfigMap are picked up by the kubelet within ~60 seconds; the
    skill catalog is rebuilt on the next investigation.

    **Mode 2 — inline skills (chart-managed):**

    Define skill contents directly in `values.yaml`. The chart creates a
    ConfigMap, mounts it at `/etc/holmes/skills/<name>/SKILL.md`, and adds
    that path to `custom_skill_paths` automatically:

    ```yaml
    customSkills:
      dns-troubleshooting:
        content: |
          ---
          description: Troubleshooting DNS resolution failures
          ---

          ## Goal
          Diagnose and resolve DNS resolution issues in the cluster.

          ## Workflow
          1. Check CoreDNS pods
          2. Test DNS resolution from an affected pod
          3. Check for NetworkPolicies blocking DNS
    ```

    Under the hood the chart sets the `CUSTOM_SKILL_PATHS` environment
    variable on the Holmes pod (comma-separated list).

=== "Config File"

    Add skill directory paths to `~/.holmes/config.yaml`:

    ```yaml
    custom_skill_paths:
      - /path/to/my-skills/
      - /path/to/team-skills/
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
