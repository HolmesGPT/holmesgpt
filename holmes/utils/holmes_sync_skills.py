import logging
from datetime import datetime, timezone

from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal
from holmes.plugins.skills.skill_loader import (
    SkillSource,
    load_filesystem_skills,
)

# HolmesCustomSkills.status values. The column exists to surface a malformed SKILL.md, so
# both are reachable: "ok" for a skill that parsed, "error" for one that did not.
STATUS_OK = "ok"
STATUS_ERROR = "error"

# Prefix marking a row as synced from a git repo; the rest of the value is the repo URL.
# Parsed by the UI (robusta-frontend custom-skill-source.ts) -- keep the format in sync.
GIT_SOURCE_PREFIX = "git:"

# How a SkillSource is labelled in the HolmesCustomSkills.source column. Filesystem skills
# load as SkillSource.USER and are reported as "custom" -- except skills whose path belongs
# to a configured git skill repo, which are reported as "git:<repo url>" so the UI can show
# which repo they sync from (older UIs treat the unrecognised value as plain custom).
SOURCE_LABELS = {
    SkillSource.USER: "custom",
    SkillSource.BUILTIN: "builtin",
}


def holmes_sync_skills_status(dal: SupabaseDal, config: Config) -> None:
    """Mirror this cluster's filesystem + builtin skills into HolmesCustomSkills.

    Purely so the UI can list them: the filesystem remains the source of truth and Holmes
    keeps executing these from disk. Runs at startup and on the periodic refresh, alongside
    holmes_sync_toolsets_status.

    Loads through load_filesystem_skills so only builtin + filesystem skills are collected --
    global and personal skills already live in HolmesRunbooks and must not be duplicated
    into the mirror. That loader also reports whether every source was readable, which is
    what makes it safe to prune on an empty result.

    Best-effort: any failure is logged and swallowed, because a display-only mirror must
    never prevent Holmes from starting.
    """
    try:
        if not config.cluster_name:
            logging.warning("Cluster name is missing; skipping custom skills sync.")
            return

        # Reports whether every skill source was readable, which the prune below depends on.
        # An empty result alone cannot distinguish "the last skill was deleted" from "the
        # ConfigMap is not mounted yet" -- and only the first should prune the mirror.
        loaded = load_filesystem_skills(config.all_skill_paths)

        repo_manager = config.skill_repo_manager

        # A repo with no checkout contributes no rows, so it cannot be reported
        # per-skill; log it here so the reason a configured repo's skills are
        # missing from the page is visible next to the sync that omitted them.
        for name, reason in repo_manager.unsynced_repos().items():
            logging.warning(
                f"Skill repo '{name}' has no checkout, so none of its skills are "
                f"mirrored to the platform: {reason}"
            )

        def source_label(source: SkillSource, source_path) -> str:
            repo = repo_manager.repo_for_path(source_path)
            return f"{GIT_SOURCE_PREFIX}{repo.url}" if repo else SOURCE_LABELS[source]

        # UTC-aware: a naive timestamp would be interpreted in the database session's
        # timezone, so updated_at would not reflect the real sync time off-UTC.
        updated_at = datetime.now(timezone.utc).isoformat()
        # Keyed by skill_name, because the batch upsert conflicts on
        # (account_id, cluster_id, skill_name) and PostgreSQL refuses an
        # ON CONFLICT DO UPDATE that touches the same row twice. Two rows sharing a name do
        # not resolve last-write-wins -- they abort the whole statement, and since the prune
        # runs after the upsert one collision would silently kill the entire sync.
        by_name: dict[str, dict] = {}

        def row(skill_name, source, description, content, source_path, status, error):
            return {
                "account_id": dal.account_id,
                "cluster_id": config.cluster_name,
                "skill_name": skill_name,
                "source": source,
                "description": description,
                "content": content,
                "source_path": source_path,
                "status": status,
                "error": error,
                "updated_at": updated_at,
            }

        for skill in loaded.skills:
            if skill.source in SOURCE_LABELS:
                by_name[skill.name] = row(
                    skill.name,
                    source_label(skill.source, skill.source_path),
                    skill.description,
                    skill.content,
                    # Stable published path, not the sha-bearing worktree the
                    # scan resolved to -- that changes on every push and the UI
                    # shows it verbatim.
                    repo_manager.display_path(skill.source_path),
                    STATUS_OK,
                    None,
                )

        # A SKILL.md that failed to parse gets a row too. Without this the columns could
        # never hold anything but "ok": the loader drops unparseable skills, so nothing
        # broken ever reached the row builder and a user's malformed file simply vanished
        # from the UI rather than showing up as broken.
        #
        # Keyed on the same skill_name a successful parse would have produced (the
        # normalized directory name), so a file that starts failing replaces its own healthy
        # row. Written AFTER the healthy rows and allowed to overwrite them: when two
        # configured paths hold the same directory name and one is malformed, the error is
        # the thing worth surfacing -- a broken skill the user cannot see is exactly what
        # this feature exists to fix.
        for failure in loaded.failed_skills:
            # failed_skills only yields problems that carry a skill_name, but the
            # field is Optional on the model, so bind it for the type checker.
            skill_name = failure.skill_name
            if skill_name and failure.source in SOURCE_LABELS:
                by_name[skill_name] = row(
                    skill_name,
                    source_label(failure.source, failure.source_path),
                    # Nullable, and there is nothing trustworthy to put here -- the parse
                    # that would have produced them is what failed.
                    None,
                    None,
                    repo_manager.display_path(failure.source_path),
                    STATUS_ERROR,
                    failure.error,
                )

        rows = list(by_name.values())

        # Conservative: prune only when EVERY source was readable. A partially-readable load
        # must not delete the rows for the part that failed, and a fully-unreadable one must
        # not wipe the list -- while a clean load of zero skills genuinely means "the last
        # skill was deleted" and must prune.
        dal.sync_skills(rows, config.cluster_name, prune=loaded.sources_ok)
    except Exception:
        logging.exception("Failed to sync custom skills", exc_info=True)
