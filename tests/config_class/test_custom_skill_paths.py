from unittest.mock import patch

from holmes.config import Config
from tests.git_skill_repo_utils import make_skill_repo


def test_config_custom_skill_paths_from_file(tmp_path):
    """Test that custom_skill_paths is loaded from config file."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\nTest content\n"
    )

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"model: gpt-4\ncustom_skill_paths:\n  - {tmp_path}\n")

    config = Config.load_from_file(config_file)

    assert config.custom_skill_paths is not None
    assert len(config.custom_skill_paths) == 1


def test_config_custom_skill_paths_empty(tmp_path):
    """Test that empty custom_skill_paths list is handled correctly."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4\ncustom_skill_paths: []\n")

    config = Config.load_from_file(config_file)

    assert config.custom_skill_paths is not None
    assert len(config.custom_skill_paths) == 0


def test_config_custom_skill_paths_not_specified(tmp_path):
    """Test that custom_skill_paths defaults to empty list when not specified."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4\n")

    config = Config.load_from_file(config_file)

    assert config.custom_skill_paths is not None
    assert len(config.custom_skill_paths) == 0


def test_config_custom_skill_paths_passed_to_toolset_manager(tmp_path):
    """Test that custom_skill_paths is passed to ToolsetManager."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\nTest content\n"
    )

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"model: gpt-4\ncustom_skill_paths:\n  - {tmp_path}\n")

    config = Config.load_from_file(config_file)
    toolset_manager = config.toolset_manager

    assert toolset_manager.custom_skill_paths is not None
    assert len(toolset_manager.custom_skill_paths) == 1


def test_config_get_skill_catalog_with_custom_paths(tmp_path):
    """Test that Config.get_skill_catalog() loads skills from custom paths."""
    skill_dir = tmp_path / "dns-troubleshooting"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: dns-troubleshooting\ndescription: Fix DNS issues\n---\n## Steps\n1. Check CoreDNS\n"
    )

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"model: gpt-4\ncustom_skill_paths:\n  - {tmp_path}\n")

    config = Config.load_from_file(config_file)
    catalog = config.get_skill_catalog()

    assert catalog is not None
    skill_names = [s.name for s in catalog.skills]
    assert "dns-troubleshooting" in skill_names


@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_load_from_env_parses_custom_skill_paths(mock_cluster, monkeypatch):
    """CUSTOM_SKILL_PATHS env var becomes a clean list on the loaded Config."""
    monkeypatch.setenv("CUSTOM_SKILL_PATHS", "/tmp/a,/tmp/b , ,/tmp/c")
    config = Config.load_from_env()
    assert config.custom_skill_paths == ["/tmp/a", "/tmp/b", "/tmp/c"]


@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_load_from_env_no_skill_paths_when_unset(mock_cluster, monkeypatch):
    monkeypatch.delenv("CUSTOM_SKILL_PATHS", raising=False)
    config = Config.load_from_env()
    assert config.custom_skill_paths == []


def test_load_from_file_falls_back_to_env_when_file_omits_paths(tmp_path, monkeypatch):
    """When config.yaml omits custom_skill_paths, the env var fills it in."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4\n")
    monkeypatch.setenv("CUSTOM_SKILL_PATHS", "/etc/holmes/skills")

    config = Config.load_from_file(config_file)
    assert config.custom_skill_paths == ["/etc/holmes/skills"]


def test_load_from_file_config_paths_take_precedence_over_env(tmp_path, monkeypatch):
    """Paths set in the config file win over the env var."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"model: gpt-4\ncustom_skill_paths:\n  - {tmp_path}\n")
    monkeypatch.setenv("CUSTOM_SKILL_PATHS", "/etc/holmes/skills")

    config = Config.load_from_file(config_file)
    assert len(config.custom_skill_paths) == 1
    assert str(config.custom_skill_paths[0]) == str(tmp_path)


# ── git skill repos (skill_repos config / SKILL_REPOS env) ──


def test_config_skill_repos_feed_the_skill_catalog(tmp_path, monkeypatch):
    """skill_repos in config.yaml is cloned and its skills reach get_skill_catalog."""
    repo = make_skill_repo(tmp_path / "repo", {"git-sourced-skill": "1. From git"})
    monkeypatch.setenv("SKILL_REPOS_DIR", str(tmp_path / "checkouts"))

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"model: gpt-4\nskill_repos:\n  - url: file://{repo}\n")

    config = Config.load_from_file(config_file)
    catalog = config.get_skill_catalog()

    assert catalog is not None
    assert "git-sourced-skill" in [s.name for s in catalog.skills]


@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_load_from_env_parses_skill_repos(mock_cluster, monkeypatch):
    monkeypatch.setenv(
        "SKILL_REPOS",
        '[{"url": "github.com/acme/skills.git", "sub_path": "skills"}]',
    )
    config = Config.load_from_env()
    assert len(config.skill_repos) == 1
    assert config.skill_repos[0].url == "https://github.com/acme/skills.git"
    assert config.skill_repos[0].sub_path == "skills"


def test_load_from_file_falls_back_to_env_skill_repos(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4\n")
    monkeypatch.setenv("SKILL_REPOS", '[{"url": "github.com/acme/skills.git"}]')

    config = Config.load_from_file(config_file)
    assert [r.url for r in config.skill_repos] == ["https://github.com/acme/skills.git"]


def test_duplicate_derived_repo_names_do_not_break_the_request_path(
    monkeypatch, caplog
):
    """A repo-name collision must not turn every chat request into a 500.

    An omitted `name` is derived from the URL's last path segment, so
    .../team-a/skills.git and .../team-b/skills.git both become "skills".
    GitSkillRepoManager rejects that, and because the manager is built lazily
    from a property reached per request via all_skill_paths, the failed
    construction was retried on every request -- a skills misconfiguration
    became a total outage. Degrade to no git-synced skills instead, loudly.
    """
    monkeypatch.setenv(
        "SKILL_REPOS",
        '[{"url": "https://github.com/team-a/skills.git"},'
        ' {"url": "https://github.com/team-b/skills.git"}]',
    )
    config = Config()
    config._apply_env_fallbacks()
    assert [r.name for r in config.skill_repos] == ["skills", "skills"]

    with caplog.at_level("ERROR"):
        first = config.all_skill_paths
        second = config.all_skill_paths  # the per-request retry

    assert first == [] and second == []
    assert "misconfigured" in caplog.text and "duplicate names" in caplog.text
