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

Instead of a Personal Access Token, authenticate with a [GitHub App](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps). `skillRepos` reads a static token from a Secret and cannot mint the App's short-lived installation tokens, so this method keeps the init-container approach: the repo is re-cloned on pod restart only, not on the periodic refresh. The init container generates a JWT from the App's private key, exchanges it for a short-lived installation token (valid 1 hour), and clones with it. The App's private key is still mounted into the init container, but the credential used to reach your repo is the installation token, which expires after an hour — so a leaked clone URL or `.git/config` goes stale on its own, unlike a Personal Access Token.

Create the App, generate a private key, and install it on your skills repo by following steps 1–4 in [GitHub MCP — Using a GitHub App](../data-sources/builtin-toolsets/github-mcp.md#using-a-github-app). For skills the App only needs the **Contents: Read-only** repository permission (plus Metadata, which GitHub adds automatically).

=== "Holmes Helm Chart"

    Have Holmes re-clone the repo on every pod restart. An init container exchanges the GitHub App credentials for an installation token, pulls the repo into an `emptyDir` shared with the main Holmes container, and a `customSkillPaths` entry registers the directory.

    **1. Create a Secret with the GitHub App credentials:**

    ```bash
    kubectl create secret generic holmes-github-app \
      -n <holmes-namespace> \
      --from-literal=GITHUB_APP_ID=<YOUR_APP_ID> \
      --from-literal=GITHUB_APP_INSTALLATION_ID=<YOUR_INSTALLATION_ID> \
      --from-file=GITHUB_APP_PRIVATE_KEY=/path/to/private-key.pem
    ```

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
        image: alpine:3.22
        envFrom:
          - secretRef:
              name: holmes-github-app
        env:
          - name: GIT_REPO
            value: github.com/<org>/<repo>.git
          - name: GIT_BRANCH
            value: main
        command: ["/bin/sh", "-c"]
        args:
          - |
            set -e
            apk add --no-cache git openssl curl >/dev/null

            b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

            KEY_FILE=$(mktemp)
            printf '%s' "$GITHUB_APP_PRIVATE_KEY" > "$KEY_FILE"

            NOW=$(date +%s)
            HEADER=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
            PAYLOAD=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((NOW - 60))" "$((NOW + 540))" "$GITHUB_APP_ID" | b64url)
            SIGNATURE=$(printf '%s.%s' "$HEADER" "$PAYLOAD" | openssl dgst -sha256 -sign "$KEY_FILE" -binary | b64url)
            rm -f "$KEY_FILE"

            TOKEN=$(curl -sf -X POST \
              -H "Authorization: Bearer ${HEADER}.${PAYLOAD}.${SIGNATURE}" \
              -H "Accept: application/vnd.github+json" \
              "https://api.github.com/app/installations/${GITHUB_APP_INSTALLATION_ID}/access_tokens" \
              | sed -n 's/.*"token" *: *"\([^"]*\)".*/\1/p')

            if [ -z "$TOKEN" ]; then
              echo "Failed to obtain a GitHub App installation token" >&2
              exit 1
            fi

            rm -rf /skills-repo/* /skills-repo/.[!.]* /skills-repo/..?* 2>/dev/null || true
            git clone --depth 1 --branch "$GIT_BRANCH" \
              "https://x-access-token:${TOKEN}@${GIT_REPO}" \
              /skills-repo
        volumeMounts:
          - name: skills-repo
            mountPath: /skills-repo

    customSkillPaths:
      - /etc/holmes/skills-git/skills   # subdirectory inside the repo where SKILL.md files live
    ```

    Adjust — everything install-specific lives in the `env:` block, so the script body can be copied verbatim:

    - `GIT_REPO` — your repo, without the scheme (for example `github.com/acme/holmes-skills.git`).
    - `GIT_BRANCH` — branch you push skills to. Check your repo's actual default: GitHub creates new repos with `main`, but older repos are often still `master`, and the clone fails outright on a wrong branch name.
    - `customSkillPaths` — point at the subdirectory inside the repo that contains skill folders. If skills are in the repo root, use `/etc/holmes/skills-git`.

    The init container installs `git`, `openssl`, and `curl` at startup, which requires egress to the Alpine package CDN. If your cluster restricts egress, build a small image with those packages preinstalled and drop the `apk add` line.

    **3. Refresh after changes.** The clone runs only on pod startup. After pushing skill changes to the tracked branch, roll the Holmes Deployment:

    ```bash
    kubectl rollout restart deploy/<release>-holmes -n <holmes-namespace>
    ```

=== "Robusta Helm Chart"

    Have Holmes re-clone the repo on every pod restart. An init container exchanges the GitHub App credentials for an installation token, pulls the repo into an `emptyDir` shared with the main Holmes container, and a `customSkillPaths` entry registers the directory.

    **1. Create a Secret with the GitHub App credentials:**

    ```bash
    kubectl create secret generic holmes-github-app \
      -n <robusta-namespace> \
      --from-literal=GITHUB_APP_ID=<YOUR_APP_ID> \
      --from-literal=GITHUB_APP_INSTALLATION_ID=<YOUR_INSTALLATION_ID> \
      --from-file=GITHUB_APP_PRIVATE_KEY=/path/to/private-key.pem
    ```

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
          image: alpine:3.22
          envFrom:
            - secretRef:
                name: holmes-github-app
          env:
            - name: GIT_REPO
              value: github.com/<org>/<repo>.git
            - name: GIT_BRANCH
              value: main
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e
              apk add --no-cache git openssl curl >/dev/null

              b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

              KEY_FILE=$(mktemp)
              printf '%s' "$GITHUB_APP_PRIVATE_KEY" > "$KEY_FILE"

              NOW=$(date +%s)
              HEADER=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
              PAYLOAD=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((NOW - 60))" "$((NOW + 540))" "$GITHUB_APP_ID" | b64url)
              SIGNATURE=$(printf '%s.%s' "$HEADER" "$PAYLOAD" | openssl dgst -sha256 -sign "$KEY_FILE" -binary | b64url)
              rm -f "$KEY_FILE"

              TOKEN=$(curl -sf -X POST \
                -H "Authorization: Bearer ${HEADER}.${PAYLOAD}.${SIGNATURE}" \
                -H "Accept: application/vnd.github+json" \
                "https://api.github.com/app/installations/${GITHUB_APP_INSTALLATION_ID}/access_tokens" \
                | sed -n 's/.*"token" *: *"\([^"]*\)".*/\1/p')

              if [ -z "$TOKEN" ]; then
                echo "Failed to obtain a GitHub App installation token" >&2
                exit 1
              fi

              rm -rf /skills-repo/* /skills-repo/.[!.]* /skills-repo/..?* 2>/dev/null || true
              git clone --depth 1 --branch "$GIT_BRANCH" \
                "https://x-access-token:${TOKEN}@${GIT_REPO}" \
                /skills-repo
          volumeMounts:
            - name: skills-repo
              mountPath: /skills-repo

      customSkillPaths:
        - /etc/holmes/skills-git/skills   # subdirectory inside the repo where SKILL.md files live
    ```

    Adjust — everything install-specific lives in the `env:` block, so the script body can be copied verbatim:

    - `GIT_REPO` — your repo, without the scheme (for example `github.com/acme/holmes-skills.git`).
    - `GIT_BRANCH` — branch you push skills to. Check your repo's actual default: GitHub creates new repos with `main`, but older repos are often still `master`, and the clone fails outright on a wrong branch name.
    - `customSkillPaths` — point at the subdirectory inside the repo that contains skill folders. If skills are in the repo root, use `/etc/holmes/skills-git`.

    The init container installs `git`, `openssl`, and `curl` at startup, which requires egress to the Alpine package CDN. If your cluster restricts egress, build a small image with those packages preinstalled and drop the `apk add` line.

    **3. Refresh after changes.** The clone runs only on pod startup. After pushing skill changes to the tracked branch, roll the Holmes Deployment:

    ```bash
    kubectl rollout restart deploy/robusta-holmes -n <robusta-namespace>
    ```

=== "Holmes CLI"

    Mint a short-lived installation token from the App credentials and clone with it:

    ```bash
    set -eu

    APP_ID=<YOUR_APP_ID>
    INSTALLATION_ID=<YOUR_INSTALLATION_ID>
    KEY_FILE=/path/to/private-key.pem
    GIT_REPO=github.com/<org>/<repo>.git
    GIT_BRANCH=main

    b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

    NOW=$(date +%s)
    HEADER=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
    PAYLOAD=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((NOW - 60))" "$((NOW + 540))" "$APP_ID" | b64url)
    SIGNATURE=$(printf '%s.%s' "$HEADER" "$PAYLOAD" | openssl dgst -sha256 -sign "$KEY_FILE" -binary | b64url)

    TOKEN=$(curl -sf -X POST \
      -H "Authorization: Bearer ${HEADER}.${PAYLOAD}.${SIGNATURE}" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/app/installations/${INSTALLATION_ID}/access_tokens" \
      | sed -n 's/.*"token" *: *"\([^"]*\)".*/\1/p')

    if [ -z "$TOKEN" ]; then
      echo "Failed to obtain a GitHub App installation token" >&2
      exit 1
    fi

    git clone --depth 1 --branch "$GIT_BRANCH" \
      "https://x-access-token:${TOKEN}@${GIT_REPO}"
    ```

    Then point `custom_skill_paths` at the clone in `~/.holmes/config.yaml`:

    ```yaml
    custom_skill_paths:
      - /path/to/your-skills-clone/
    ```

    Installation tokens expire after 1 hour — re-run the token snippet and `git pull` with a fresh token whenever you want to pick up new or updated skills.

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
