import os

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.skills import RobustaSkillInstruction
from holmes.plugins.skills.skill_loader import Skill, SkillCatalog, SkillSource
from holmes.plugins.toolsets.skills.skills_fetcher import (
    SkillsFetcher,
    SkillsToolset,
)
from tests.conftest import create_mock_tool_invoke_context

TEST_SKILLS_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "skills"
)


def test_SkillsFetcher_not_found():
    skills_fetch_tool = SkillsFetcher(SkillsToolset())
    result = skills_fetch_tool._invoke(
        {"skill_id": "nonexistent-skill"},
        context=create_mock_tool_invoke_context(),
    )
    assert result.status == StructuredToolResultStatus.ERROR
    assert result.error is not None


def test_SkillsFetcher_with_skill_catalog():
    catalog = SkillCatalog(
        skills=[
            Skill(
                name="test-skill",
                description="A test skill",
                content="## Steps\n1. Do something",
                source=SkillSource.USER,
            )
        ]
    )
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), skill_catalog=catalog)
    result = skills_fetch_tool._invoke(
        {"skill_id": "test-skill"},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.error is None
    assert result.data is not None
    assert "Do something" in result.data


def test_SkillsFetcher_empty_id():
    skills_fetch_tool = SkillsFetcher(SkillsToolset())
    result = skills_fetch_tool._invoke(
        {"skill_id": ""},
        context=create_mock_tool_invoke_context(),
    )
    assert result.status == StructuredToolResultStatus.ERROR


def test_SkillsFetcher_one_liner():
    catalog = SkillCatalog(
        skills=[
            Skill(
                name="test-skill",
                description="A test skill",
                content="content",
                source=SkillSource.USER,
            )
        ]
    )
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), skill_catalog=catalog)
    assert (
        skills_fetch_tool.get_parameterized_one_liner({"skill_id": "test-skill"})
        == "Skills: Fetch Skill test-skill"
    )


# ── personal skills are resolved per-request, not from the cached toolset ──


def _context_for_user(user_id):
    """A tool invoke context carrying an end-user id, as the chat path supplies."""
    ctx = create_mock_tool_invoke_context()
    return ctx.model_copy(update={"request_context": {"user_id": user_id}})


class _PersonalDal:
    """Stub DAL whose personal-skill content is keyed by (skill_id, user_id)."""

    enabled = True
    # Holmes's own service identity; must never be used to scope personal skills
    user_id = "holmes-service-user"

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def get_personal_skill_content(self, skill_id, user_id):
        self.calls.append((skill_id, user_id))
        return self._rows.get((skill_id, user_id))

    def get_skill_content(self, skill_id):
        return None


def _personal_instruction(id_, title, body):
    return RobustaSkillInstruction(
        id=id_, title=title, symptom="when it breaks", instruction=body
    )


def test_SkillsFetcher_resolves_personal_skill_for_requesting_user():
    """A personal skill id is absent from the cached catalog and must still resolve."""
    dal = _PersonalDal(
        {("uuid-a", "user-a"): _personal_instruction("uuid-a", "A skill", "Step A")}
    )
    fetcher = SkillsFetcher(SkillsToolset(), skill_catalog=None, dal=dal)

    # The invariant this whole design rests on: the id is NOT in the declared list, because
    # that list is baked into a description shared by every user.
    assert "uuid-a" not in fetcher.available_skills

    result = fetcher._invoke({"skill_id": "uuid-a"}, context=_context_for_user("user-a"))

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "Step A" in result.data
    assert ("uuid-a", "user-a") in dal.calls


class TestSkillIdParameterDescription:
    """The declared id list omits personal skills, so it must not claim to be closed.

    A model that reads the parameter description as a hard contract will refuse to fetch a
    personal skill it can see in the prompt catalog -- observed in production as "the skill
    ID ... is not in my available skill list to fetch directly" -- even though _invoke would
    have resolved it. These tests pin the wording that caused that.
    """

    @staticmethod
    def _description(catalog):
        fetcher = SkillsFetcher(SkillsToolset(), skill_catalog=catalog, dal=None)
        return fetcher.parameters["skill_id"].description

    def _catalog(self, *names):
        return SkillCatalog(
            skills=[
                Skill(
                    name=n,
                    description="d",
                    content="c",
                    source=SkillSource.REMOTE,
                    display_name=n,
                )
                for n in names
            ]
        )

    def test_does_not_claim_a_closed_set(self):
        description = self._description(self._catalog("uuid-global"))

        assert "Must be one of" not in description
        assert "not exhaustive" in description
        assert "uuid-global" in description

    def test_empty_catalog_does_not_render_an_empty_allow_list(self):
        """With no global/filesystem skills the old text was a bare "Must be one of: ",
        i.e. an empty allow-list -- the worst case for a personal-skills-only user.

        The replacement must not mention a list of known ids either: referring to one that
        was never rendered ("that list is not exhaustive", "does not appear above") is the
        same failure in a different costume.
        """
        for catalog in (None, SkillCatalog(skills=[])):
            description = self._description(catalog)

            assert "Must be one of" not in description
            assert "Known ids include" not in description
            assert "not exhaustive" not in description
            assert "does not appear above" not in description
            # still tells the model where the ids actually come from
            assert "Skill Catalog" in description
            assert "personal" in description

    def test_points_the_model_at_the_prompt_catalog(self):
        description = self._description(self._catalog("uuid-global"))

        assert "Skill Catalog" in description
        assert "personal" in description


def test_SkillsFetcher_does_not_leak_personal_skill_across_users():
    """Two users served by the SAME cached toolset instance must not see each other's.

    The toolset is built once and cached with a key that ignores account/user, so this is
    the leak that would occur if personal skills were baked into the catalog.
    """
    dal = _PersonalDal(
        {
            ("uuid-a", "user-a"): _personal_instruction("uuid-a", "A skill", "Step A"),
            ("uuid-b", "user-b"): _personal_instruction("uuid-b", "B skill", "Step B"),
        }
    )
    fetcher = SkillsFetcher(SkillsToolset(), skill_catalog=None, dal=dal)

    result_a = fetcher._invoke(
        {"skill_id": "uuid-a"}, context=_context_for_user("user-a")
    )
    assert "Step A" in result_a.data

    # user-b asking for user-a's skill id gets nothing back
    result_b = fetcher._invoke(
        {"skill_id": "uuid-a"}, context=_context_for_user("user-b")
    )
    assert result_b.status == StructuredToolResultStatus.ERROR
    assert "Step A" not in (result_b.data or "")


def test_SkillsFetcher_no_personal_lookup_without_user_id():
    """With no end-user id (server-initiated run) the personal lookup is never attempted."""
    dal = _PersonalDal(
        {("uuid-a", "holmes-service-user"): _personal_instruction("uuid-a", "S", "X")}
    )
    fetcher = SkillsFetcher(SkillsToolset(), skill_catalog=None, dal=dal)

    fetcher._invoke({"skill_id": "uuid-a"}, context=create_mock_tool_invoke_context())

    assert dal.calls == []
