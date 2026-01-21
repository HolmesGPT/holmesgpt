from holmes.plugins.toolsets.git import GitHubConfig
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_github_config():
    example = build_config_example(GitHubConfig)

    assert example["git_repo"] == "robusta-dev/holmesgpt"
    assert example["git_credentials"] == "your_git_credentials"
    assert example["git_branch"] == "main"
    assert example["git_url"] == "https://api.github.com"

