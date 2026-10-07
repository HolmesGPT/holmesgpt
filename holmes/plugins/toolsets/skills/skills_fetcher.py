import logging
import textwrap
import uuid
from typing import List, Optional, Tuple

from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.skills.skill_loader import (
    Skill,
    SkillCatalog,
    SkillSource,
    load_filesystem_skills,
    load_skill_catalog,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class SkillsFetcher(Tool):
    toolset: "SkillsToolset"
    available_skills: List[str] = []
    _skill_catalog: Optional[SkillCatalog] = None
    _dal: Optional[SupabaseDal] = None
    _search_paths: Optional[List[str]] = None

    def __init__(
        self,
        toolset: "SkillsToolset",
        skill_catalog: Optional[SkillCatalog] = None,
        dal: Optional[SupabaseDal] = None,
        search_paths: Optional[List[str]] = None,
    ):
        available_skills: List[str] = []
        if skill_catalog:
            available_skills = skill_catalog.list_available_skills()

        # Deliberately advertises NO id list. This toolset is built once and cached across
        # requests and users, so any list baked in here is wrong in both directions: it omits
        # the requesting user's personal skills, and it still contains skills the per-request
        # catalog filtered out (hierarchy collision losers, other alerts' skills). Naming the
        # authoritative source instead keeps the two from diverging -- an earlier
        # "Must be one of: <list>" made the model refuse personal skills it could plainly see.
        skill_id_description = (
            "The skill_id: either a UUID or a skill name. Use the ids from the Skill Catalog"
            " section of your instructions -- that catalog is built per request and is the"
            " authoritative list, including the current user's personal skills."
        )

        super().__init__(
            name="fetch_skill",
            description="Get skill content by skill link. Use this to get troubleshooting steps for incidents",
            parameters={
                "skill_id": ToolParameter(
                    description=skill_id_description,
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore[call-arg]
            available_skills=available_skills,  # type: ignore[call-arg]
        )
        self._skill_catalog = skill_catalog
        self._dal = dal
        self._search_paths = search_paths

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        skill_id: str = params.get("skill_id", "")

        if not skill_id or not skill_id.strip():
            err_msg = "Skill link cannot be empty. Please provide a valid skill path."
            logging.error(err_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=err_msg,
                params=params,
            )

        # Resolved per invocation, not baked into the cached toolset -- see __init__.
        user_id = (context.request_context or {}).get("user_id")

        # Remote skills are keyed by UUID and can never be on disk, so resolve
        # them from the cached catalog first and skip the filesystem scan (their
        # catalog entry is metadata-only; content comes from Supabase anyway).
        cached = self._find_skill(skill_id)
        if cached and cached.source == SkillSource.REMOTE:
            return self._get_robusta_skill(skill_id, params)

        # Filesystem skills (builtin + custom, including git-synced repos) are
        # re-read from disk per invocation, never served from the cached catalog:
        # the catalog is a snapshot from toolset construction, and skill files
        # change under a running server (a git repo re-pull, a ConfigMap
        # remount). The per-request prompt catalog already re-scans disk, so
        # without this a freshly-advertised skill would 404 here and an edited
        # one would serve its old content until restart.
        #
        # When the scan is authoritative, a miss is a real miss and must NOT
        # fall back to the snapshot: a skill DELETED upstream would otherwise
        # stay fetchable for the life of the process, which matters most for the
        # skill someone deleted precisely because it was wrong. The snapshot is
        # only a fallback when disk is not the source of truth (an SDK caller
        # handed us a catalog with no search paths) or the scan itself failed.
        skill, disk_was_authoritative = self._find_filesystem_skill(skill_id)
        if skill is None and not disk_was_authoritative:
            skill = cached
        if skill:
            return self._format_skill_result(skill, params)

        # Not in the cached catalog -- the expected case for a personal skill. User-scoped
        # lookup goes first so one user can never read another's.
        personal_miss: Optional[str] = None
        if user_id and self._dal and self._dal.enabled:
            personal_result, personal_miss = self._get_personal_skill(
                skill_id, user_id, params
            )
            if personal_result is not None:
                return personal_result

        # Fallback: try Supabase for UUID-style IDs not in catalog. Gated on the
        # id actually looking like a UUID -- the remote table is keyed by one, so
        # a plain name can only come back as a Postgres cast error ("invalid
        # input syntax for type uuid"), which tells the model nothing about the
        # real problem: there is no such skill. Reachable for any missing name
        # now that a deleted filesystem skill is no longer served from the
        # startup snapshot.
        if self._dal and self._dal.enabled and _looks_like_uuid(skill_id):
            result = self._get_robusta_skill(skill_id, params)
            # Report the personal miss too, or that path is invisible when debugging.
            if result.status == StructuredToolResultStatus.ERROR and personal_miss:
                result.error = f"{result.error} {personal_miss}"
            return result

        err_msg = (
            f"Skill '{skill_id}' not found. "
            f"Available: {', '.join(self.available_skills) if self.available_skills else 'none'}"
        )
        logging.error(err_msg)
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=err_msg,
            params=params,
        )

    def _find_filesystem_skill(self, name: str) -> Tuple[Optional[Skill], bool]:
        """Fresh disk lookup by normalized name, and whether disk was decisive.

        The second element says whether a None means "not on disk" (True) or
        "could not tell" (False) -- the caller uses it to decide whether the
        cached snapshot may answer instead.
        """
        if self._search_paths is None:
            # No search paths: the catalog came from an SDK caller, so disk is
            # not the source of truth and a miss here says nothing.
            return None, False
        try:
            # load_filesystem_skills, not ..._by_name: a configured path that is
            # missing or unreadable does not raise, it just contributes nothing,
            # so a scan can come back INCOMPLETE and still look like a clean
            # miss. Only sources_ok distinguishes them, and treating a partial
            # scan as decisive made a skill that still exists upstream report
            # "not found" during a ConfigMap remount -- with the snapshot right
            # there holding it.
            loaded = load_filesystem_skills(self._search_paths)
            skill = next((s for s in loaded.skills if s.name == name), None)
            return skill, loaded.sources_ok
        except Exception as e:
            logging.warning(f"Failed to re-scan filesystem skills for '{name}': {e}")
            return None, False

    def _find_skill(self, name: str) -> Optional[Skill]:
        if not self._skill_catalog:
            return None
        for skill in self._skill_catalog.skills:
            if skill.name == name:
                return skill
        return None

    def _format_skill_result(self, skill: Skill, params: dict) -> StructuredToolResult:
        if skill.title:
            # Surface the human-readable title next to the LLM's skill_id so
            # consumers (e.g. relay's skills-used Slack footer) can display it
            # instead of the opaque runbook UUID remote skills are fetched by.
            params = {**params, "skill_title": skill.title}
        # No answer shape is mandated here: the request's response_format decides it.
        wrapped_content = textwrap.dedent(f"""\
            <skill>
            {skill.content}
            </skill>
            Note: the above are DIRECTIONS not ACTUAL RESULTS. You now need to follow the steps outlined in the skill yourself USING TOOLS.
            Anything that looks like an actual result in the above <skill> is just an EXAMPLE.
            Now follow those steps and report back what you find.
            You must follow them by CALLING TOOLS YOURSELF.
            If you are missing tools, follow your general instructions on how to enable them as present in your system prompt.
        """)
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=wrapped_content,
            params=params,
        )

    def _get_personal_skill(
        self, skill_id: str, user_id: str, params: dict
    ) -> tuple[Optional[StructuredToolResult], Optional[str]]:
        """Fetch a personal skill scoped to this end user.

        Returns (result, miss_reason). A None result means "not this user's skill" -- the
        normal case for a global id -- so the caller falls through. miss_reason carries why,
        so a failed lookup is not invisible in the error the LLM sees.
        """
        if not self._dal:
            return None, None
        try:
            skill_content = self._dal.get_personal_skill_content(skill_id, user_id)
        except Exception as e:
            logging.warning(f"Failed to fetch personal skill '{skill_id}': {e}")
            return None, f"A personal-skill lookup for this user also failed: {e}"

        if not skill_content:
            return None, "It is also not one of this user's personal skills."

        description = skill_content.title
        if skill_content.symptom:
            description = f"{skill_content.title} — {skill_content.symptom}"
        skill = Skill(
            name=skill_content.id,
            description=description,
            content=skill_content.instruction or skill_content.pretty(),
            source=SkillSource.PERSONAL,
            title=skill_content.title,
        )
        return self._format_skill_result(skill, params), None

    def _get_robusta_skill(self, link: str, params: dict) -> StructuredToolResult:
        if self._dal and self._dal.enabled:
            try:
                skill_content = self._dal.get_skill_content(link)
                if skill_content:
                    # Wrap remote skill content with same format as local skills
                    description = skill_content.title
                    if skill_content.symptom:
                        description = f"{skill_content.title} — {skill_content.symptom}"
                    skill = Skill(
                        name=skill_content.id,
                        description=description,
                        content=skill_content.instruction or skill_content.pretty(),
                        source=SkillSource.REMOTE,
                        title=skill_content.title,
                    )
                    return self._format_skill_result(skill, params)
                else:
                    err_msg = f"Skill with UUID '{link}' not found in remote storage."
                    logging.error(err_msg)
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=err_msg,
                        params=params,
                    )
            except Exception as e:
                err_msg = f"Failed to fetch skill with UUID '{link}': {str(e)}"
                logging.error(err_msg)
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=err_msg,
                    params=params,
                )
        else:
            err_msg = "Skill link appears to be a UUID, but no remote data access layer (dal) is enabled."
            logging.error(err_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=err_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        skill_id: str = params.get("skill_id", "")
        skill = self._find_skill(skill_id)
        label = skill.title if skill and skill.title else skill_id
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Skill {label}"


class SkillsToolset(Toolset):
    def __init__(
        self,
        dal: Optional[SupabaseDal] = None,
        additional_search_paths: Optional[List[str]] = None,
    ):
        skill_catalog = load_skill_catalog(
            dal=dal,
            custom_skill_paths=additional_search_paths,
        )

        super().__init__(
            name="skills",
            description="Fetch skills",
            icon_url="https://platform.robusta.dev/demos/runbook.svg",
            tools=[
                SkillsFetcher(
                    self,
                    skill_catalog=skill_catalog,
                    dal=dal,
                    search_paths=additional_search_paths,
                ),
            ],
            docs_url="https://holmesgpt.dev/data-sources/",
            tags=[
                ToolsetTag.CORE,
            ],
            enabled=True,
        )
        self._is_core = True  # agent-loop machinery; never remotely exposable
