import json

import pytest
import yaml
from typer.testing import CliRunner

from holmes.main import app
from holmes.plugins.toolsets.bash.common.cli_prefixes import (
    extract_claude_code_bash_prefixes,
    parse_claude_code_bash_permission,
)


runner = CliRunner()


def test_parse_claude_code_bash_permission_strips_trailing_wildcard_only():
    entry = "Bash(aws sts assume-role --role-arn arn:aws:iam::123:role/Admin:*)"

    assert (
        parse_claude_code_bash_permission(entry)
        == "aws sts assume-role --role-arn arn:aws:iam::123:role/Admin"
    )


def test_parse_claude_code_bash_permission_strips_space_star_wildcard():
    assert parse_claude_code_bash_permission("Bash(kubectl get *)") == "kubectl get"


def test_extract_claude_code_bash_prefixes_warns_on_unsupported_entries():
    prefixes, ignored = extract_claude_code_bash_prefixes(
        {
            "permissions": {
                "allow": [
                    "Bash(kubectl get:*)",
                    "Bash(kubectl get:*)",
                    "Read(**)",
                    "Bahs(ls:*)",
                    5,
                ]
            }
        }
    )

    assert prefixes == ["kubectl get"]
    assert ignored == ["Read(**)", "Bahs(ls:*)", "5"]


def test_extract_claude_code_bash_prefixes_requires_allow_list():
    with pytest.raises(ValueError, match=r"'permissions\.allow' must be a list"):
        extract_claude_code_bash_prefixes({"permissions": {"allow": "Bash(ls:*)"}})


def test_extract_claude_code_bash_prefixes_ignores_allows_overlapping_denies():
    prefixes, ignored = extract_claude_code_bash_prefixes(
        {
            "permissions": {
                "allow": [
                    "Bash(kubectl:*)",
                    "Bash(helm list:*)",
                    "Bash(git status)",
                ],
                "deny": [
                    "Bash(kubectl delete:*)",
                    "Bash(helm:*)",
                ],
            }
        }
    )

    assert prefixes == ["git status"]
    assert ignored == ["Bash(kubectl:*)", "Bash(helm list:*)"]


def test_import_from_claude_code_merges_with_existing_prefixes(tmp_path):
    settings_file = tmp_path / "settings.json"
    output_file = tmp_path / "bash_approved_prefixes.yaml"

    settings_file.write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "Bash(aws elbv2 describe-load-balancers:*)",
                        "Bash(kubectl get pods)",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    output_file.write_text(
        yaml.safe_dump({"approved_prefixes": ["kubectl get"]}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "toolset",
            "bash",
            "import-from-claude-code",
            "--input",
            str(settings_file),
            "--output",
            str(output_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(output_file.read_text(encoding="utf-8")) == {
        "approved_prefixes": [
            "aws elbv2 describe-load-balancers",
            "kubectl get",
            "kubectl get pods",
        ]
    }


def test_import_from_claude_code_replace_ignores_existing_prefixes(tmp_path):
    settings_file = tmp_path / "settings.json"
    output_file = tmp_path / "bash_approved_prefixes.yaml"

    settings_file.write_text(
        json.dumps({"permissions": {"allow": ["Bash(kubectl logs:*)"]}}),
        encoding="utf-8",
    )
    output_file.write_text(
        yaml.safe_dump({"approved_prefixes": ["kubectl get"]}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "toolset",
            "bash",
            "import-from-claude-code",
            "--input",
            str(settings_file),
            "--output",
            str(output_file),
            "--replace",
        ],
    )

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(output_file.read_text(encoding="utf-8")) == {
        "approved_prefixes": ["kubectl logs"]
    }


def test_import_from_claude_code_dry_run_does_not_write(tmp_path):
    settings_file = tmp_path / "settings.json"
    output_file = tmp_path / "bash_approved_prefixes.yaml"

    settings_file.write_text(
        json.dumps({"permissions": {"allow": ["Bash(helm list:*)"]}}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "toolset",
            "bash",
            "import-from-claude-code",
            "--input",
            str(settings_file),
            "--output",
            str(output_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not output_file.exists()
    assert yaml.safe_load(result.output) == {"approved_prefixes": ["helm list"]}
