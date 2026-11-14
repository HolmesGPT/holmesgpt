from unittest.mock import patch

from typer.testing import CliRunner

from holmes.config import Config
from holmes.main import app


def _github_settings(args):
    seen = {}

    def fake_create_github_source(self):
        seen.update(url=self.github_url, query=self.github_query)
        raise RuntimeError("stop before fetching issues")

    with patch.object(Config, "create_github_source", fake_create_github_source):
        CliRunner().invoke(app, ["investigate", "github", *args])
    return seen


def test_config_file_github_url_and_query_are_used(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        'github_url: "https://ghe.example.com/api/v3"\n'
        'github_query: "is:issue is:open label:incident"\n'
    )

    assert _github_settings(["--config", str(config_file)]) == {
        "url": "https://ghe.example.com/api/v3",
        "query": "is:issue is:open label:incident",
    }


def test_cli_github_url_and_query_override_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text('github_url: "https://ghe.example.com/api/v3"\n')

    assert _github_settings(
        [
            "--config",
            str(config_file),
            "--github-url",
            "https://other.example.com/api/v3",
            "--github-query",
            "is:issue label:bug",
        ]
    ) == {"url": "https://other.example.com/api/v3", "query": "is:issue label:bug"}


def test_github_url_and_query_defaults_without_config(tmp_path):
    assert _github_settings(["--config", str(tmp_path / "missing.yaml")]) == {
        "url": "https://api.github.com",
        "query": "is:issue is:open",
    }
