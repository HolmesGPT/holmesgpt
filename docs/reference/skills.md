# Skills

!!! note "Requires Holmes 0.26.0+"

    Skills are supported starting in Holmes 0.26.0. Earlier versions use the legacy runbook system. See [Migrating from Runbooks](#migrating-from-runbooks) below.

Skills are step-by-step troubleshooting guides Holmes follows when investigating issues. When a user asks a question or an alert fires, Holmes matches relevant skills from its catalog, fetches them with the `fetch_skill` tool, and executes the steps — calling tools to gather data and reporting what it found at each step.

Holmes ships with [built-in skills](#built-in-skills) that work out of the box. This page shows how to add your own.

## Loading Custom Skills

There are three ways to load custom skills, covered below. Within each, pick your deployment — Holmes OSS (CLI or Helm Chart) or HolmesGPT Enterprise (the Robusta Helm Chart) — to get the exact configuration to copy. Your deployment choice is remembered across the whole site, and you can change it anytime.

### From a GitHub Repository

Keep skills version-controlled in a Git repo so they can be reviewed, versioned, and shared across a team.

For private repos there are two ways to authenticate: a fine-grained [Personal Access Token](#using-a-personal-access-token) (simplest) or a [GitHub App](#using-a-github-app) (short-lived auto-expiring tokens, not tied to a personal account).

Holmes syncs the repos itself: it clones each configured repo at startup and re-pulls it periodically (every `TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS`, default 5 minutes), so pushed skill changes reach a running agent automatically — no pod restart needed. Configure as many repos as you like; skills from all of them are merged.

#### Using a Personal Access Token

=== "Holmes Helm Chart"

    **1. Create a Secret with a GitHub Personal Access Token.** Use a fine-grained PAT scoped to a single repo with `Contents: Read`:

    ```bash
    kubectl create secret generic holmes-skills-git-credentials \
      -n <holmes-namespace> \
      --from-literal=token='<PAT>'
    ```

    For a public repo, omit the Secret and the `tokenSecret` block below.

    **2. Add the repo to your Helm values:**

    ```yaml
    skillRepos:
      - url: https://github.com/<org>/<repo>.git
        branch: main        # optional; defaults to the repo's default branch
        subPath: skills     # optional; subdirectory inside the repo where SKILL.md dirs live
        tokenSecret:
          name: holmes-skills-git-credentials
          key: token
    ```

    That's it — Holmes keeps the repo in sync. After pushing skill changes to the tracked branch, they show up within the refresh interval (default 5 minutes).

=== "Robusta Helm Chart"

    **1. Create a Secret with a GitHub Personal Access Token.** Use a fine-grained PAT scoped to a single repo with `Contents: Read`:

    ```bash
    kubectl create secret generic holmes-skills-git-credentials \
      -n <robusta-namespace> \
      --from-literal=token='<PAT>'
    ```

    For a public repo, omit the Secret and the `tokenSecret` block below.

    **2. Add the repo to your `generated_values.yaml`:**

    ```yaml
    enableHolmesGPT: true
    holmes:
      skillRepos:
        - url: https://github.com/<org>/<repo>.git
          branch: main        # optional; defaults to the repo's default branch
          subPath: skills     # optional; subdirectory inside the repo where SKILL.md dirs live
          tokenSecret:
            name: holmes-skills-git-credentials
            key: token
    ```

    That's it — Holmes keeps the repo in sync. After pushing skill changes to the tracked branch, they show up within the refresh interval (default 5 minutes).

=== "Holmes CLI"

    Add the repo to `~/.holmes/config.yaml`. Holmes syncs it at the start of each run:

    ```yaml
    skill_repos:
      - url: https://github.com/<org>/<repo>.git
        branch: main        # optional; defaults to the repo's default branch
        sub_path: skills    # optional; subdirectory inside the repo where SKILL.md dirs live
        token_env: GITHUB_SKILLS_TOKEN   # env var holding the PAT; omit for public repos
    ```

    ```bash
    export GITHUB_SKILLS_TOKEN='<PAT>'
    holmes ask "why is my pod crashing?"
    ```

    Alternatively, clone the repo yourself and point `custom_skill_paths` at the clone — then `git pull` manually whenever you want updates.

#### Using a GitHub App

Instead of a Personal Access Token, authenticate with a [GitHub App](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps). GitHub Apps have no static token: Holmes signs a JWT with the App's private key, exchanges it for a short-lived installation token (valid 1 hour), and fetches with that — re-minting automatically on the periodic sync. A leaked token goes stale on its own, and the credential is not tied to a personal account.

Create the App, generate a private key, and install it on your skills repo by following steps 1-4 in [GitHub MCP — Using a GitHub App](../data-sources/builtin-toolsets/github-mcp.md#using-a-github-app). For skills the App only needs the **Contents: Read-only** repository permission (plus Metadata, which GitHub adds automatically).

=== "Holmes Helm Chart"

    **1. Create a Secret with the App's private key:**

    ```bash
    kubectl create secret generic holmes-github-app \
      -n <holmes-namespace> \
      --from-file=GITHUB_APP_PRIVATE_KEY=/path/to/private-key.pem
    ```

    **2. Add the repo to your Helm values:**

    ```yaml
    skillRepos:
      - url: https://github.com/<org>/<repo>.git
        branch: main        # optional; defaults to the repo's default branch
        subPath: skills     # optional; subdirectory inside the repo where SKILL.md dirs live
        githubApp:
          appId: "<YOUR_APP_ID>"
          installationId: "<YOUR_INSTALLATION_ID>"
          privateKeySecret:
            name: holmes-github-app
            key: GITHUB_APP_PRIVATE_KEY
    ```

    That's it — Holmes keeps the repo in sync, minting a fresh installation token whenever the cached one nears expiry. After pushing skill changes to the tracked branch, they show up within the refresh interval (default 5 minutes).

    For GitHub Enterprise Server, add `apiUrl: https://<ghe-host>/api/v3` under `githubApp`.

=== "Robusta Helm Chart"

    **1. Create a Secret with the App's private key:**

    ```bash
    kubectl create secret generic holmes-github-app \
      -n <robusta-namespace> \
      --from-file=GITHUB_APP_PRIVATE_KEY=/path/to/private-key.pem
    ```

    **2. Add the repo to your `generated_values.yaml`:**

    ```yaml
    enableHolmesGPT: true
    holmes:
      skillRepos:
        - url: https://github.com/<org>/<repo>.git
          branch: main        # optional; defaults to the repo's default branch
          subPath: skills     # optional; subdirectory inside the repo where SKILL.md dirs live
          githubApp:
            appId: "<YOUR_APP_ID>"
            installationId: "<YOUR_INSTALLATION_ID>"
            privateKeySecret:
              name: holmes-github-app
              key: GITHUB_APP_PRIVATE_KEY
    ```

    That's it — Holmes keeps the repo in sync, minting a fresh installation token whenever the cached one nears expiry. After pushing skill changes to the tracked branch, they show up within the refresh interval (default 5 minutes).

    For GitHub Enterprise Server, add `apiUrl: https://<ghe-host>/api/v3` under `githubApp`.

=== "Holmes CLI"

    Add the repo to `~/.holmes/config.yaml`. Holmes syncs it at the start of each run, minting the installation token from the App's private key:

    ```yaml
    skill_repos:
      - url: https://github.com/<org>/<repo>.git
        branch: main        # optional; defaults to the repo's default branch
        sub_path: skills    # optional; subdirectory inside the repo where SKILL.md dirs live
        github_app_id: "<YOUR_APP_ID>"
        github_app_installation_id: "<YOUR_INSTALLATION_ID>"
        github_app_private_key_env: GITHUB_APP_PRIVATE_KEY
    ```

    ```bash
    export GITHUB_APP_PRIVATE_KEY="$(cat /path/to/private-key.pem)"
    holmes ask "why is my pod crashing?"
    ```


### From a Bitbucket Repository

Same pattern as GitHub — only the credentials differ. Bitbucket uses a [Repository Access Token](https://support.atlassian.com/bitbucket-cloud/docs/repository-access-tokens/) with the `x-token-auth` username.

=== "Holmes Helm Chart"

    **1. Create a Secret with a Bitbucket Repository Access Token.** Create the token under *Repository settings → Access tokens* with the `Repositories: Read` scope:

    ```bash
    kubectl create secret generic holmes-skills-git-credentials \
      -n <holmes-namespace> \
      --from-literal=token='<repository-access-token>'
    ```

    For a public repo, omit the Secret and the `tokenSecret`/`username` entries below.

    **2. Add the repo to your Helm values:**

    ```yaml
    skillRepos:
      - url: https://bitbucket.org/<workspace>/<repo>.git
        branch: main        # optional; defaults to the repo's default branch
        subPath: skills     # optional; subdirectory inside the repo where SKILL.md dirs live
        username: x-token-auth
        tokenSecret:
          name: holmes-skills-git-credentials
          key: token
    ```

    That's it — Holmes keeps the repo in sync. After pushing skill changes to the tracked branch, they show up within the refresh interval (default 5 minutes).

=== "Robusta Helm Chart"

    **1. Create a Secret with a Bitbucket Repository Access Token.** Create the token under *Repository settings → Access tokens* with the `Repositories: Read` scope:

    ```bash
    kubectl create secret generic holmes-skills-git-credentials \
      -n <robusta-namespace> \
      --from-literal=token='<repository-access-token>'
    ```

    For a public repo, omit the Secret and the `tokenSecret`/`username` entries below.

    **2. Add the repo to your `generated_values.yaml`:**

    ```yaml
    enableHolmesGPT: true
    holmes:
      skillRepos:
        - url: https://bitbucket.org/<workspace>/<repo>.git
          branch: main        # optional; defaults to the repo's default branch
          subPath: skills     # optional; subdirectory inside the repo where SKILL.md dirs live
          username: x-token-auth
          tokenSecret:
            name: holmes-skills-git-credentials
            key: token
    ```

    That's it — Holmes keeps the repo in sync. After pushing skill changes to the tracked branch, they show up within the refresh interval (default 5 minutes).

=== "Holmes CLI"

    Add the repo to `~/.holmes/config.yaml`. Holmes syncs it at the start of each run:

    ```yaml
    skill_repos:
      - url: https://bitbucket.org/<workspace>/<repo>.git
        branch: main        # optional; defaults to the repo's default branch
        sub_path: skills    # optional; subdirectory inside the repo where SKILL.md dirs live
        username: x-token-auth
        token_env: BITBUCKET_SKILLS_TOKEN   # env var holding the token; omit for public repos
    ```

    ```bash
    export BITBUCKET_SKILLS_TOKEN='<repository-access-token>'
    holmes ask "why is my pod crashing?"
    ```

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
