import json
import subprocess
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

from pydantic import SecretStr

from holmes.core.tools import (
    ToolsetStatusEnum,
    ToolsetType,
    YAMLTool,
    YAMLToolset,
)
from holmes.utils.doctor import (
    CheckStatus,
    collect_doctor_report,
    dumps_doctor_report,
    render_doctor_report,
)


def _toolset(
    name: str,
    status: ToolsetStatusEnum,
    enabled: bool = True,
    error: Optional[str] = None,
) -> YAMLToolset:
    return YAMLToolset(
        name=name,
        description=f"{name} tools",
        enabled=enabled,
        status=status,
        type=ToolsetType.BUILTIN,
        error=error,
        tools=[YAMLTool(name=f"{name}_ping", description="ping", command="true")],
    )


def _config(*, api_key: Optional[str] = "sk-test", model: str = "gpt-4.1") -> MagicMock:
    config = MagicMock()
    config.api_key = SecretStr(api_key) if api_key else None
    config.model = model
    config._model_source = "via $MODEL"
    return config


def _collect(**kwargs):
    defaults = dict(
        config_file=Path("/tmp/does-not-exist-holmes-doctor.yaml"),
        skip_toolsets=True,
        environ={},
        python_version=(3, 12, 3),
        get_version_fn=lambda: "0.0.0-test",
        which_fn=lambda _name: None,
        run_command=lambda *a, **k: subprocess.CompletedProcess(
            args=["kubectl"], returncode=1, stdout="", stderr="no context"
        ),
        load_config=lambda _path: _config(),
        list_toolsets=lambda **_kw: [],
    )
    defaults.update(kwargs)
    return collect_doctor_report(**defaults)


def _check(report, name: str):
    matches = [c for c in report.checks if c.name == name]
    assert matches, f"missing check {name}: {[c.name for c in report.checks]}"
    return matches[-1]


def test_python_too_old_fails():
    report = _collect(python_version=(3, 9, 18))
    check = _check(report, "python")
    assert check.status == CheckStatus.FAIL
    assert report.ok is False
    assert "3.9.18" in check.summary


def test_python_3_14_warns():
    report = _collect(python_version=(3, 14, 0))
    check = _check(report, "python")
    assert check.status == CheckStatus.WARN
    assert report.ok is True


def test_supported_python_ok():
    report = _collect(python_version=(3, 12, 3))
    check = _check(report, "python")
    assert check.status == CheckStatus.OK
    assert report.python_version == "3.12.3"


def test_missing_llm_credentials_fails():
    report = _collect(load_config=lambda _path: _config(api_key=None), environ={})
    check = _check(report, "llm_credentials")
    assert check.status == CheckStatus.FAIL
    assert report.ok is False
    payload = report.to_dict()
    dumped = json.dumps(payload)
    assert "sk-test" not in dumped
    assert any(
        entry["name"] == "OPENAI_API_KEY" and entry["set"] is False
        for entry in payload["llm_credentials"]
    )


def test_openai_env_counts_as_credentials():
    report = _collect(
        load_config=lambda _path: _config(api_key=None),
        environ={"OPENAI_API_KEY": "sk-live-secret-value"},
    )
    check = _check(report, "llm_credentials")
    assert check.status == CheckStatus.OK
    dumped = dumps_doctor_report(report)
    assert "sk-live-secret-value" not in dumped
    assert "OPENAI_API_KEY" in dumped


def test_config_api_key_counts_as_credentials():
    report = _collect(
        load_config=lambda _path: _config(api_key="from-file"),
        environ={},
    )
    check = _check(report, "llm_credentials")
    assert check.status == CheckStatus.OK
    assert "from-file" not in dumps_doctor_report(report)


def test_aws_key_without_secret_warns():
    report = _collect(environ={"AWS_ACCESS_KEY_ID": "AKIATEST", "OPENAI_API_KEY": "x"})
    check = _check(report, "aws_credentials")
    assert check.status == CheckStatus.WARN
    assert "AKIATEST" not in dumps_doctor_report(report)


def test_missing_google_credentials_file_warns(tmp_path: Path):
    missing = tmp_path / "no-such-sa.json"
    report = _collect(
        environ={
            "OPENAI_API_KEY": "x",
            "GOOGLE_APPLICATION_CREDENTIALS": str(missing),
        }
    )
    check = _check(report, "google_credentials_file")
    assert check.status == CheckStatus.WARN


def test_kubectl_missing_is_warning_not_failure():
    report = _collect(which_fn=lambda _name: None)
    check = _check(report, "kubernetes")
    assert check.status == CheckStatus.WARN
    assert report.kubernetes["kubectl_found"] is False


def test_kubectl_current_context_ok():
    def run_command(*_a, **_k):
        return subprocess.CompletedProcess(
            args=["kubectl"], returncode=0, stdout="kind-kind\n", stderr=""
        )

    report = _collect(
        which_fn=lambda _name: "/usr/bin/kubectl", run_command=run_command
    )
    check = _check(report, "kubernetes")
    assert check.status == CheckStatus.OK
    assert report.kubernetes["context"] == "kind-kind"


def test_kubectl_timeout_warns():
    def run_command(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="kubectl", timeout=3)

    report = _collect(
        which_fn=lambda _name: "/usr/bin/kubectl", run_command=run_command
    )
    check = _check(report, "kubernetes")
    assert check.status == CheckStatus.WARN
    assert "timed out" in (report.kubernetes["error"] or "")


def test_toolset_summary_counts_failed_and_unconfigured():
    toolsets: List[YAMLToolset] = [
        _toolset("kubernetes/core", ToolsetStatusEnum.ENABLED),
        _toolset(
            "prometheus/metrics", ToolsetStatusEnum.FAILED, enabled=True, error="no url"
        ),
        _toolset("datadog/logs", ToolsetStatusEnum.FAILED, enabled=False),
        _toolset("old/one", ToolsetStatusEnum.DISABLED, enabled=False),
    ]
    report = _collect(
        skip_toolsets=False,
        list_toolsets=lambda **_kw: toolsets,
    )
    assert report.toolsets == {
        "enabled": 1,
        "failed": 1,
        "unconfigured": 1,
        "disabled": 1,
        "failed_names": ["prometheus/metrics"],
    }
    check = _check(report, "toolsets")
    assert check.status == CheckStatus.WARN
    assert "prometheus/metrics" in (check.detail or "")


def test_skip_toolsets_omits_toolset_check():
    report = _collect(skip_toolsets=True)
    assert report.toolsets is None
    assert all(c.name != "toolsets" for c in report.checks)


def test_toolset_load_error_is_warning():
    def boom(**_kw):
        raise RuntimeError("cache unreadable")

    report = _collect(skip_toolsets=False, list_toolsets=boom)
    check = _check(report, "toolsets")
    assert check.status == CheckStatus.WARN
    assert "cache unreadable" in (check.detail or "")
    assert report.ok is True


def test_config_load_error_still_reports_env_credentials():
    def boom(_path):
        raise ValueError("bad yaml")

    report = _collect(
        load_config=boom,
        environ={"ANTHROPIC_API_KEY": "secret-should-not-leak"},
    )
    assert _check(report, "config_load").status == CheckStatus.WARN
    assert _check(report, "llm_credentials").status == CheckStatus.OK
    assert "secret-should-not-leak" not in dumps_doctor_report(report)


def test_existing_config_file_is_ok(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model: gpt-4.1\n", encoding="utf-8")
    report = _collect(config_file=config_path)
    check = _check(report, "config")
    assert check.status == CheckStatus.OK
    assert report.config_exists is True


def test_render_does_not_print_secret_values():
    from io import StringIO

    from rich.console import Console

    report = _collect(
        load_config=lambda _path: _config(api_key="super-secret-key"),
        environ={"OPENAI_API_KEY": "sk-also-secret"},
    )
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    render_doctor_report(report, console)
    text = buf.getvalue()
    assert "super-secret-key" not in text
    assert "sk-also-secret" not in text
    assert "llm_credentials" in text
