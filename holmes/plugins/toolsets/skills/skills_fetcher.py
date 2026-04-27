import logging
import textwrap
from typing import List, Optional

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
    load_skill_catalog,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class SkillsFetcher(Tool):
    toolset: "SkillsToolset"
    available_skills: List[str] = []
    _skill_catalog: Optional[SkillCatalog] = None
    _dal: Optional[SupabaseDal] = None

    def __init__(
        self,
        toolset: "SkillsToolset",
        skill_catalog: Optional[SkillCatalog] = None,
        dal: Optional[SupabaseDal] = None,
    ):
        available_skills: List[str] = []
        if skill_catalog:
            available_skills = skill_catalog.list_available_skills()

        skill_list = ", ".join([f'"{s}"' for s in available_skills])

        super().__init__(
            name="fetch_skill",
            description="Get skill content by skill link. Use this to get troubleshooting steps for incidents",
            parameters={
                "skill_id": ToolParameter(
                    description=f"The skill_id: either a UUID or a skill name. Must be one of: {skill_list}",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore[call-arg]
            available_skills=available_skills,  # type: ignore[call-arg]
        )
        self._skill_catalog = skill_catalog
        self._dal = dal

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

        # Look up in skill catalog by name — remote skills have empty content
        # (catalog only stores metadata), so fetch full content from Supabase
        skill = self._find_skill(skill_id)
        if skill and skill.source == SkillSource.REMOTE:
            return self._get_robusta_skill(skill_id, params)
        elif skill:
            return self._format_skill_result(skill, params)

        # Fallback: try Supabase for UUID-style IDs not in catalog
        if self._dal and self._dal.enabled:
            return self._get_robusta_skill(skill_id, params)

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

    def _find_skill(self, name: str) -> Optional[Skill]:
        if not self._skill_catalog:
            return None
        for skill in self._skill_catalog.skills:
            if skill.name == name:
                return skill
        return None

    def _format_skill_result(self, skill: Skill, params: dict) -> StructuredToolResult:
        wrapped_content = textwrap.dedent(f"""\
            <skill>
            {skill.content}
            </skill>
            Note: the above are DIRECTIONS not ACTUAL RESULTS. You now need to follow the steps outlined in the skill yourself USING TOOLS.
            Anything that looks like an actual result in the above <skill> is just an EXAMPLE.
            Now follow those steps and report back what you find.
            You must follow them by CALLING TOOLS YOURSELF.
            If you are missing tools, follow your general instructions on how to enable them as present in your system prompt.

            Assuming the above skill is relevant, you MUST start your response (after calling tools to investigate) with:
            "I found a skill named [skill name/description] and used it to troubleshoot:"

            Then list each step with ✅ for completed steps and ❌ for steps you couldn't complete.

            <example>
                I found a skill named **Troubleshooting Erlang Issues** and used it to troubleshoot:

                1. ✅ *Check BEAM VM memory usage* - 87% allocated (3.2GB used of 4GB limit)
                2. ✅ *Review GC logs* - 15 full GC cycles in last 30 minutes, avg pause time 2.3s
                3. ✅ *Verify Erlang application logs* - `** exception error: out of memory in process <0.139.0> called by gen_server:handle_msg/6`
                4. ❌ *Could not analyze process mailbox sizes* - Observer tool not enabled in container. Enable remote shell or observer_cli for process introspection.
                5. ✅ *Check pod memory limits* - container limit 4Gi, requests 2Gi
                6. ✅ *Verify BEAM startup arguments* - `+S 4:4 +P 1048576`, no memory instrumentation flags enabled
                7. ❌ *Could not retrieve APM traces* - Datadog traces toolset is disabled. You can enable it by following https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/
                8. ❌ *Could not query Erlang metrics* - Prometheus integration is not connected. Enable it via https://holmesgpt.dev/data-sources/builtin-toolsets/prometheus/
                9. ✅ *Examine recent deployments* - app version 2.1.3 deployed 4 hours ago, coincides with memory spike
                10. ❌ *Could not check Stripe API status* - No toolset for Stripe integration exists. To monitor Stripe or similar third-party APIs, add a [custom toolset](https://holmesgpt.dev/data-sources/custom-toolsets/) or use a [remote MCP server](https://holmesgpt.dev/data-sources/remote-mcp-servers/)

                **Root cause:** Memory leak in `gen_server` logic introduced in v2.1.3. BEAM VM hitting memory limit, causing out-of-memory crashes.

                **Fix:** Roll back to v2.1.2 or increase memory limit to 6GB as a temporary workaround.
            </example>
        """)
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=wrapped_content,
            params=params,
        )

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
        path: str = params.get("skill_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Skill {path}"


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
                SkillsFetcher(self, skill_catalog=skill_catalog, dal=dal),
            ],
            docs_url="https://holmesgpt.dev/data-sources/",
            tags=[
                ToolsetTag.CORE,
            ],
            enabled=True,
        )
