# Skills

Skills are troubleshooting guides that Holmes can load into its skill catalog. Holmes matches a skill by its description; when a skill clearly applies to a question or alert, Holmes fetches the skill content with the `fetch_skill` tool and follows its instructions.

## Structure

Each local skill is a directory containing a `SKILL.md` file with YAML frontmatter and a markdown body.

```text
holmes/plugins/skills/builtin/
  dns-troubleshooting/
    SKILL.md
```

Built-in skills live under `holmes/plugins/skills/builtin/`. Custom skills can live in any directory, or be provided as direct `SKILL.md` file paths, through `custom_skill_paths`.

## SKILL.md Format

```markdown
---
name: dns-troubleshooting
description: Troubleshooting DNS resolution failures in Kubernetes clusters
---

## Goal
...

## Workflow
...

## Synthesize Findings
...

## Recommended Remediation Steps
...
```

`description` is required and is used for matching. `name` is optional; if omitted, Holmes uses the parent directory name and normalizes it to lowercase hyphenated form.

## Loading

Holmes scans built-in and custom skill directories up to two levels deep for `SKILL.md` files. If multiple skills use the same name, the higher-priority source wins:

1. Remote Robusta skills
2. Custom/user skills from `custom_skill_paths`
3. Built-in skills

Configure custom skill paths in Holmes config:

```yaml
custom_skill_paths:
  - /path/to/my-skills/
  - /path/to/team-skill/SKILL.md
```

## Authoring Guidance

Write skills as procedural, evidence-driven troubleshooting instructions. Include the goal and scope, sequential diagnostic steps, guidance for synthesizing findings, and remediation plus verification steps.

For user-facing configuration and migration details, see [Skills](../../../docs/reference/skills.md).
