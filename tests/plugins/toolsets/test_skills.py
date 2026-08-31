import os
import shutil
from unittest.mock import Mock

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.skills import RobustaSkillInstruction
from holmes.plugins.skills.skill_loader import (
    Skill,
    SkillCatalog,
    SkillSource,
    load_skill_catalog,
)
from holmes.plugins.toolsets.skills.skills_fetcher import (
    SkillsFetcher,
    SkillsToolset,
)
from tests.conftest import create_mock_tool_invoke_context

TEST_SKILLS_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "skills")


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

    result = fetcher._invoke(
        {"skill_id": "uuid-a"}, context=_context_for_user("user-a")
    )

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
                    title=n,
                )
                for n in names
            ]
        )

    def test_advertises_no_id_list_at_all(self):
        """No ids may appear here, however they are phrased.

        This description is cached across requests and users, so a baked-in list is wrong in
        both directions: it omits the requesting user's personal skills, and it retains
        skills the per-request catalog filtered out (hierarchy losers, other alerts'). Any
        id in this string is a claim the cached toolset cannot back.
        """
        description = self._description(self._catalog("uuid-global", "uuid-other"))

        assert "uuid-global" not in description
        assert "uuid-other" not in description
        assert "Must be one of" not in description
        assert "Known ids include" not in description

    def test_is_identical_whatever_the_cached_catalog_holds(self):
        """Nothing catalog-dependent leaks in, so the cached description cannot go stale."""
        variants = {
            self._description(None),
            self._description(SkillCatalog(skills=[])),
            self._description(self._catalog("uuid-global")),
            self._description(self._catalog("a", "b", "c")),
        }

        assert len(variants) == 1

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


REMOTE_SKILL_UUID = "3e0f2f6a-9f0e-4d5f-8f7a-2b1c9d8e7f6a"


def _mock_remote_dal(title: str = "Erlang Debugging") -> Mock:
    dal = Mock()
    dal.enabled = True
    skill_content = Mock()
    skill_content.id = REMOTE_SKILL_UUID
    skill_content.title = title
    skill_content.symptom = "BEAM VM crashes"
    skill_content.instruction = "## Steps\n1. Check BEAM memory"
    dal.get_skill_content.return_value = skill_content
    return dal


def test_SkillsFetcher_remote_skill_result_carries_title():
    """Remote skills are fetched by their runbook UUID; the result params must
    carry the human-readable title so chat surfaces (e.g. the Slack skills
    footer) can display it instead of the UUID."""
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), dal=_mock_remote_dal())
    result = skills_fetch_tool._invoke(
        {"skill_id": REMOTE_SKILL_UUID},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.params["skill_id"] == REMOTE_SKILL_UUID
    assert result.params["skill_title"] == "Erlang Debugging"


def test_SkillsFetcher_local_skill_result_has_no_title():
    """Local skills are already fetched by a readable name — no title field."""
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
    result = skills_fetch_tool._invoke(
        {"skill_id": "test-skill"},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "skill_title" not in result.params


def test_SkillsFetcher_one_liner_uses_title_for_remote_skills():
    """The tool-call display line shows the title, not the runbook UUID."""
    catalog = SkillCatalog(
        skills=[
            Skill(
                name=REMOTE_SKILL_UUID,
                description="Erlang Debugging — BEAM VM crashes",
                content="",
                source=SkillSource.REMOTE,
                source_path=REMOTE_SKILL_UUID,
                title="Erlang Debugging",
            )
        ]
    )
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), skill_catalog=catalog)
    assert (
        skills_fetch_tool.get_parameterized_one_liner({"skill_id": REMOTE_SKILL_UUID})
        == "Skills: Fetch Skill Erlang Debugging"
    )


# ── filesystem skills are re-read from disk per invocation, never from the ──
# ── snapshot the toolset was built with (live refresh of git-synced repos) ──


def _write_fs_skill(dir_path, name, body):
    skill_dir = dir_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\ndescription: {name}\n---\n## Steps\n{body}\n"
    )


def test_SkillsFetcher_serves_skill_added_after_toolset_construction(tmp_path):
    toolset = SkillsToolset(additional_search_paths=[str(tmp_path)])
    fetcher = toolset.tools[0]

    # Simulates a git re-pull / ConfigMap remount landing a new skill on disk
    # after the toolset (and its catalog snapshot) was built.
    _write_fs_skill(tmp_path, "new-skill", "Do the new thing")

    result = fetcher._invoke(
        {"skill_id": "new-skill"}, context=create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "Do the new thing" in result.data


def test_SkillsFetcher_serves_edited_content_not_the_startup_snapshot(tmp_path):
    _write_fs_skill(tmp_path, "dns-debug", "old steps")
    toolset = SkillsToolset(additional_search_paths=[str(tmp_path)])
    fetcher = toolset.tools[0]

    _write_fs_skill(tmp_path, "dns-debug", "brand new steps")

    result = fetcher._invoke(
        {"skill_id": "dns-debug"}, context=create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "brand new steps" in result.data
    assert "old steps" not in result.data


def test_SkillsFetcher_stops_serving_a_skill_deleted_from_disk(tmp_path):
    """A skill removed upstream must stop being fetchable, not fall back to the snapshot.

    The catalog snapshot taken at toolset construction outlives the file. Serving
    from it on a miss kept a deleted skill fetchable for the life of the process
    -- worst for the skill someone deleted precisely because it was wrong.
    """
    _write_fs_skill(tmp_path, "pod-oom", "Check memory limits")
    toolset = SkillsToolset(additional_search_paths=[str(tmp_path)])
    fetcher = toolset.tools[0]
    # It resolves while the file is there.
    assert (
        fetcher._invoke(
            {"skill_id": "pod-oom"}, context=create_mock_tool_invoke_context()
        ).status
        == StructuredToolResultStatus.SUCCESS
    )

    shutil.rmtree(tmp_path / "pod-oom")

    result = fetcher._invoke(
        {"skill_id": "pod-oom"}, context=create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "Check memory limits" not in str(result.data)


def test_SkillsFetcher_still_uses_the_catalog_when_no_search_paths_are_set(tmp_path):
    """SDK callers hand in a catalog directly; disk is not the source of truth then."""
    _write_fs_skill(tmp_path, "sdk-skill", "from an SDK catalog")
    catalog = load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    toolset = SkillsToolset()  # no additional_search_paths
    fetcher = SkillsFetcher(toolset, skill_catalog=catalog)

    shutil.rmtree(tmp_path / "sdk-skill")

    result = fetcher._invoke(
        {"skill_id": "sdk-skill"}, context=create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "from an SDK catalog" in result.data


def test_SkillsFetcher_reports_a_missing_name_clearly_not_as_a_uuid_cast_error(
    tmp_path,
):
    """A plain name that does not exist must not be sent to the UUID-keyed table.

    The remote skills table is keyed by UUID, so a name reached it only to come
    back as "invalid input syntax for type uuid" -- which says nothing about the
    real problem. Reachable for any missing name now that a deleted filesystem
    skill is no longer served from the startup snapshot.
    """
    dal = Mock()
    dal.enabled = True
    toolset = SkillsToolset(additional_search_paths=[str(tmp_path)])
    fetcher = SkillsFetcher(toolset, search_paths=[str(tmp_path)], dal=dal)

    result = fetcher._invoke(
        {"skill_id": "no-such-skill"}, context=create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "no-such-skill" in result.error and "not found" in result.error
    assert "uuid" not in result.error.lower()
    dal.get_skill.assert_not_called() if hasattr(dal, "get_skill") else None


def test_SkillsFetcher_falls_back_to_the_snapshot_when_a_source_is_unreadable(tmp_path):
    """An INCOMPLETE scan must not be treated as a decisive miss.

    A configured path that is missing or unreadable does not raise -- it just
    contributes nothing -- so a partial scan looks exactly like a clean miss.
    Treating it as decisive made a skill that still exists upstream report "not
    found" during a ConfigMap remount, while the snapshot was holding it. The
    error even contradicted itself: "Skill 'pod-oom' not found. Available:
    dns-debug, pod-oom".
    """
    good = tmp_path / "good"
    mount = tmp_path / "mount"
    _write_fs_skill(good, "dns-debug", "dns steps")
    _write_fs_skill(mount, "pod-oom", "Check memory limits")
    toolset = SkillsToolset(additional_search_paths=[str(good), str(mount)])
    fetcher = toolset.tools[0]

    shutil.rmtree(mount)  # the mount goes away for a cycle

    skill, authoritative = fetcher._find_filesystem_skill("pod-oom")
    assert authoritative is False, "a scan missing a configured source is not decisive"
    result = fetcher._invoke(
        {"skill_id": "pod-oom"}, context=create_mock_tool_invoke_context()
    )
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "Check memory limits" in result.data


def test_SkillsFetcher_deletion_is_still_authoritative_on_a_clean_scan(tmp_path):
    """The deletion fix must survive the partial-scan fix: a clean miss still wins."""
    _write_fs_skill(tmp_path, "pod-oom", "Check memory limits")
    toolset = SkillsToolset(additional_search_paths=[str(tmp_path)])
    fetcher = toolset.tools[0]

    shutil.rmtree(tmp_path / "pod-oom")  # deleted, but the source is still readable

    skill, authoritative = fetcher._find_filesystem_skill("pod-oom")
    assert (skill, authoritative) == (None, True)
    result = fetcher._invoke(
        {"skill_id": "pod-oom"}, context=create_mock_tool_invoke_context()
    )
    assert result.status == StructuredToolResultStatus.ERROR
