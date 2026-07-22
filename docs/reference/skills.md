# Skills

!!! note "Requires Holmes 0.26.0+"

    Skills are supported starting in Holmes 0.26.0. Earlier versions use the legacy runbook system. See [Migrating from Runbooks](#migrating-from-runbooks) below.

Skills are step-by-step troubleshooting guides Holmes follows when investigating issues. When a user asks a question or an alert fires, Holmes matches relevant skills from its catalog, fetches them with the `fetch_skill` tool, and executes the steps — calling tools to gather data and reporting what it found at each step.

Holmes ships with [built-in skills](#built-in-skills) that work out of the box. This page shows how to add your own.

## Loading Custom Skills

There are three ways to load custom skills, covered below. Within each, pick your deployment — Holmes OSS (CLI or Helm Chart) or HolmesGPT Enterprise (the Robusta Helm Chart) — to get the exact configuration to copy. Your deployment choice is remembered across the whole site, and you can change it anytime.

### From a GitHub Repository

Keep skills version-controlled in a Git repo so they can be reviewed, versioned, and shared across a team.

=== "Holmes Helm Chart"

    Have Holmes re-clone the repo on every pod restart. An init container pulls the repo into an `emptyDir` shared with the main Holmes container, and a `customSkillPaths` entry registers the directory.

    **1. Create a Secret with a GitHub Personal Access Token.** Use a fine-grained PAT scoped to a single repo with `Contents: Read`:

    ```bash
    kubectl create secret generic holmes-skills-git-credentials \
      -n <holmes-namespace> \
      --from-literal=token='<PAT>'
    ```

    For a public repo, omit the Secret and drop the `oauth2:${GIT_PAT}@` segment from the clone URL below.

    **2. Add the init container, volume, and skill path to your Helm values:**

    ```yaml
    additionalVolumes:
      - name: skills-repo
        emptyDir:
          sizeLimit: 64Mi

    additionalVolumeMounts:
      - name: skills-repo
        mountPath: /etc/holmes/skills-git
        readOnly: true

    initContainers:
      - name: clone-skills
        image: alpine/git:2.45.2
        env:
          - name: GIT_PAT
            valueFrom:
              secretKeyRef:
                name: holmes-skills-git-credentials
                key: token
        command: ["/bin/sh", "-c"]
        args:
          - |
            set -e
            rm -rf /skills-repo/.git /skills-repo/* 2>/dev/null || true
            git clone --depth 1 --branch main \
              "https://oauth2:${GIT_PAT}@github.com/<org>/<repo>.git" \
              /skills-repo
        volumeMounts:
          - name: skills-repo
            mountPath: /skills-repo

    customSkillPaths:
      - /etc/holmes/skills-git/skills   # subdirectory inside the repo where SKILL.md files live
    ```

    Adjust:

    - `--branch main` — branch you push skills to.
    - `https://github.com/<org>/<repo>.git` — your repo URL.
    - `customSkillPaths` — point at the subdirectory inside the repo that contains skill folders. If skills are in the repo root, use `/etc/holmes/skills-git`.

    **3. Refresh after changes.** The clone runs only on pod startup. After pushing skill changes to the tracked branch, roll the Holmes Deployment:

    ```bash
    kubectl rollout restart deploy/<release>-holmes -n <holmes-namespace>
    ```

=== "Robusta Helm Chart"

    Have Holmes re-clone the repo on every pod restart. An init container pulls the repo into an `emptyDir` shared with the main Holmes container, and a `customSkillPaths` entry registers the directory.

    **1. Create a Secret with a GitHub Personal Access Token.** Use a fine-grained PAT scoped to a single repo with `Contents: Read`:

    ```bash
    kubectl create secret generic holmes-skills-git-credentials \
      -n <robusta-namespace> \
      --from-literal=token='<PAT>'
    ```

    For a public repo, omit the Secret and drop the `oauth2:${GIT_PAT}@` segment from the clone URL below.

    **2. Add the init container, volume, and skill path to your `generated_values.yaml`:**

    ```yaml
    enableHolmesGPT: true
    holmes:
      additionalVolumes:
        - name: skills-repo
          emptyDir:
            sizeLimit: 64Mi

      additionalVolumeMounts:
        - name: skills-repo
          mountPath: /etc/holmes/skills-git
          readOnly: true

      initContainers:
        - name: clone-skills
          image: alpine/git:2.45.2
          env:
            - name: GIT_PAT
              valueFrom:
                secretKeyRef:
                  name: holmes-skills-git-credentials
                  key: token
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e
              rm -rf /skills-repo/.git /skills-repo/* 2>/dev/null || true
              git clone --depth 1 --branch main \
                "https://oauth2:${GIT_PAT}@github.com/<org>/<repo>.git" \
                /skills-repo
          volumeMounts:
            - name: skills-repo
              mountPath: /skills-repo

      customSkillPaths:
        - /etc/holmes/skills-git/skills   # subdirectory inside the repo where SKILL.md files live
    ```

    Adjust:

    - `--branch main` — branch you push skills to.
    - `https://github.com/<org>/<repo>.git` — your repo URL.
    - `customSkillPaths` — point at the subdirectory inside the repo that contains skill folders. If skills are in the repo root, use `/etc/holmes/skills-git`.

    **3. Refresh after changes.** The clone runs only on pod startup. After pushing skill changes to the tracked branch, roll the Holmes Deployment:

    ```bash
    kubectl rollout restart deploy/robusta-holmes -n <robusta-namespace>
    ```

=== "Holmes CLI"

    Clone the repo to your machine and point `custom_skill_paths` at the clone in `~/.holmes/config.yaml`:

    ```yaml
    custom_skill_paths:
      - /path/to/your-skills-clone/
    ```

    Run `git pull` in the clone whenever you want to pick up new or updated skills.

### Inline in Helm Values

Define skills directly in your Helm values. The chart creates a ConfigMap, mounts it, and registers the path — no extra wiring. Changes take effect on the next `helm upgrade`.

!!! note "Helm only"

    Not applicable to the Holmes CLI — use [From a GitHub Repository](#from-a-github-repository) or point `custom_skill_paths` at a local directory instead.

=== "Holmes Helm Chart"

    ```yaml
    customSkills:
      dns-troubleshooting:
        content: |
          ---
          description: Troubleshoot DNS resolution failures in the cluster
          ---

          ## Goal
          Diagnose DNS issues.

          ## Workflow
          1. Check CoreDNS pods in kube-system
          2. Test DNS resolution from an affected pod
          3. Check NetworkPolicies for blocked egress to kube-system
      pod-restart-quickcheck:
        content: |
          ---
          description: Quick diagnosis for CrashLoopBackOff / restarting pods
          ---

          ## Goal
          Identify why a pod is restarting.

          ## Workflow
          1. Inspect pod status and restart count
          2. Pull previous container logs
          3. Check namespace events
    ```

=== "Robusta Helm Chart"

    ```yaml
    enableHolmesGPT: true
    holmes:
      customSkills:
        dns-troubleshooting:
          content: |
            ---
            description: Troubleshoot DNS resolution failures in the cluster
            ---

            ## Goal
            Diagnose DNS issues.

            ## Workflow
            1. Check CoreDNS pods in kube-system
            2. Test DNS resolution from an affected pod
            3. Check NetworkPolicies for blocked egress to kube-system
        pod-restart-quickcheck:
          content: |
            ---
            description: Quick diagnosis for CrashLoopBackOff / restarting pods
            ---

            ## Goal
            Identify why a pod is restarting.

            ## Workflow
            1. Inspect pod status and restart count
            2. Pull previous container logs
            3. Check namespace events
    ```

### ConfigMap or Secret (advanced)

Use this when you want to keep skill content outside `values.yaml` — for example, one ConfigMap per team, skills stored in a Secret, or skills populated by an `initContainer`. `customSkillPaths` accepts a list, so you can load skills from multiple directories at once.

Each directory must contain skills in `<skill-name>/SKILL.md` layout. Since Kubernetes ConfigMap/Secret keys cannot contain `/`, use an `items:` projection to map flat keys (e.g. `dns-troubleshooting.SKILL.md`) to that layout.

!!! note "Helm only"

    Not applicable to the Holmes CLI — use [From a GitHub Repository](#from-a-github-repository) or point `custom_skill_paths` at a local directory instead.

=== "Holmes Helm Chart"

    ```yaml
    additionalVolumes:
      - name: skills-frontend
        configMap:
          name: holmes-skills-frontend
          items:
            - key: dns-troubleshooting.SKILL.md
              path: dns-troubleshooting/SKILL.md
            - key: pod-restart-quickcheck.SKILL.md
              path: pod-restart-quickcheck/SKILL.md
      - name: skills-backend
        configMap:
          name: holmes-skills-backend
    additionalVolumeMounts:
      - name: skills-frontend
        mountPath: /etc/holmes/skills-frontend
        readOnly: true
      - name: skills-backend
        mountPath: /etc/holmes/skills-backend
        readOnly: true
    customSkillPaths:
      - /etc/holmes/skills-frontend
      - /etc/holmes/skills-backend
    ```

    Skills from all paths are merged. If two paths define the same skill name, the later one wins. Changes to mounted ConfigMaps/Secrets only take effect after a Holmes pod restart — roll the Deployment after updating skill files.

=== "Robusta Helm Chart"

    ```yaml
    enableHolmesGPT: true
    holmes:
      additionalVolumes:
        - name: skills-frontend
          configMap:
            name: holmes-skills-frontend
            items:
              - key: dns-troubleshooting.SKILL.md
                path: dns-troubleshooting/SKILL.md
              - key: pod-restart-quickcheck.SKILL.md
                path: pod-restart-quickcheck/SKILL.md
        - name: skills-backend
          configMap:
            name: holmes-skills-backend
      additionalVolumeMounts:
        - name: skills-frontend
          mountPath: /etc/holmes/skills-frontend
          readOnly: true
        - name: skills-backend
          mountPath: /etc/holmes/skills-backend
          readOnly: true
      customSkillPaths:
        - /etc/holmes/skills-frontend
        - /etc/holmes/skills-backend
    ```

    Skills from all paths are merged. If two paths define the same skill name, the later one wins. Changes to mounted ConfigMaps/Secrets only take effect after a Holmes pod restart — roll the Deployment after updating skill files.

Holmes scans each path up to 2 levels deep for `SKILL.md` files.

## Writing Skills

Each skill is a directory containing a `SKILL.md` file with YAML frontmatter and a markdown body:

```markdown
---
name: dns-troubleshooting
description: Troubleshoot DNS resolution failures in Kubernetes clusters
---

## Goal
Diagnose and resolve DNS resolution issues in the cluster.

## Workflow

1. **Check CoreDNS pods**
   * Verify pods in kube-system with label `k8s-app=kube-dns` are running
   * Check for restarts or resource pressure

2. **Test DNS resolution**
   * Resolve `kubernetes.default.svc.cluster.local` from an affected pod
   * Resolve an external domain like `google.com`

3. **Check NetworkPolicies blocking DNS**
   * List NetworkPolicies in the affected namespace
   * Verify UDP port 53 egress to kube-system is allowed

## Synthesize Findings
Correlate the outputs from each step to identify the root cause.

## Recommended Remediation Steps
* **CoreDNS down**: check resource limits and node capacity
* **NetworkPolicy blocking**: add an egress rule allowing DNS traffic
* **ConfigMap wrong**: fix the Corefile and restart CoreDNS
```

**Frontmatter:**

- `name` (optional): lowercase with hyphens. Defaults to the parent directory name.
- `description` (required): used by the LLM to match the skill to user issues. Be specific.

**Recommended body sections:**

- **Goal** — what the skill addresses
- **Workflow** — sequential steps Holmes will execute
- **Synthesize Findings** — how to interpret combined results
- **Recommended Remediation Steps** — actions based on findings

## Built-in Skills

Holmes ships with built-in skills at `holmes/plugins/skills/builtin/`. They are loaded automatically — no configuration needed. Custom skills with the same name override built-ins.

## Migrating from Runbooks

If you are upgrading from Holmes 0.25.x or older, existing runbooks need to be converted to the `SKILL.md` format.

For each runbook in your catalog:

1. Create a directory named after the runbook (lowercase, hyphens):
   ```
   my-skills/postgres-troubleshooting/
   ```

2. Create a `SKILL.md` inside it with frontmatter taken from your `catalog.json` entry, and the original markdown content as the body:
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

## Further Reading

- [Python SDK — Loading Custom Skills](python-sdk.md#loading-custom-skills) — read the resolved skill catalog programmatically.
