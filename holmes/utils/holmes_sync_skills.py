import logging
from datetime import datetime, timezone

from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal
from holmes.plugins.skills.skill_loader import (
    SkillSource,
    load_skill_catalog,
)

# How a SkillSource is labelled in the HolmesCustomSkills.source column. Every filesystem
# skill -- from a GitHub repo, inline Helm values, or a ConfigMap/Secret mount -- loads as
# SkillSource.USER and Holmes keeps no origin metadata, so they are all reported as
# "custom". Distinguishing github/inline/configmap would require new origin tagging.
SOURCE_LABELS = {
    SkillSource.USER: "custom",
    SkillSource.BUILTIN: "builtin",
}


def holmes_sync_skills_status(dal: SupabaseDal, config: Config) -> None:
    """Mirror this cluster's filesystem + builtin skills into HolmesCustomSkills.

    Purely so the UI can list them: the filesystem remains the source of truth and Holmes
    keeps executing these from disk. Runs at startup and on the periodic refresh, alongside
    holmes_sync_toolsets_status.

    Deliberately loads with dal=None so only builtin + filesystem skills are collected --
    global and personal skills already live in HolmesRunbooks and must not be duplicated
    into the mirror.

    Best-effort: any failure is logged and swallowed, because a display-only mirror must
    never prevent Holmes from starting.
    """
    try:
        if not config.cluster_name:
            logging.warning("Cluster name is missing; skipping custom skills sync.")
            return

        catalog = load_skill_catalog(
            dal=None, custom_skill_paths=config.custom_skill_paths
        )
        if not catalog or not catalog.skills:
            logging.debug("No filesystem or builtin skills found to sync.")
            return

        # UTC-aware: a naive timestamp would be interpreted in the database session's
        # timezone, so updated_at would not reflect the real sync time off-UTC.
        updated_at = datetime.now(timezone.utc).isoformat()
        rows = [
            {
                "account_id": dal.account_id,
                "cluster_id": config.cluster_name,
                "skill_name": skill.name,
                "source": SOURCE_LABELS.get(skill.source, skill.source.value),
                "description": skill.description,
                "content": skill.content,
                "source_path": skill.source_path,
                # Skills that fail to parse are logged and skipped by the loader, so
                # everything reaching here parsed cleanly.
                "status": "ok",
                "error": None,
                "updated_at": updated_at,
            }
            for skill in catalog.skills
            if skill.source in SOURCE_LABELS
        ]

        dal.sync_skills(rows, config.cluster_name)
    except Exception:
        logging.exception("Failed to sync custom skills", exc_info=True)
