"""CLI diagnostics for HolmesGPT setup.

``holmes doctor`` reports local environment, LLM credentials (presence only),
Kubernetes context, and toolset status so users can verify a CLI install
without sending a prompt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from rich.console import Console
from rich.table import Table

from holmes import get_version
from holmes.common.env_vars import DEFAULT_MODEL, TOOL_MEMORY_LIMIT_MB
from holmes.config import DEFAULT_CONFIG_LOCATION, Config
from holmes.core.tools import Toolset, ToolsetStatusEnum

# Presence-only: values are never copied into the report.
LLM_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "VERTEXAI_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
)

_MAX_FAILED_TOOLSET_NAMES = 20
_KUBECTL_TIMEOUT_SECONDS = 3.0


class CheckStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


@dataclass
class DoctorCheck:
    name: str
    status: CheckStatus
    summary: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "detail": self.detail,
        }


@dataclass
class DoctorReport:
    version: str
    python_version: str
    config_path: str
    config_exists: bool
    model: Optional[str]
    model_source: Optional[str]
    llm_credentials: List[Dict[str, Any]]
    kubernetes: Dict[str, Any]
    tool_memory_limit_mb: int
    toolsets: Optional[Dict[str, Any]]
    checks: List[DoctorCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(check.status == CheckStatus.FAIL for check in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "version": self.version,
            "python_version": self.python_version,
            "config": {"path": self.config_path, "exists": self.config_exists},
            "model": {"name": self.model, "source": self.model_source},
            "llm_credentials": list(self.llm_credentials),
            "kubernetes": dict(self.kubernetes),
            "tool_memory_limit_mb": self.tool_memory_limit_mb,
            "toolsets": self.toolsets,
            "checks": [check.to_dict() for check in self.checks],
        }


def _format_python_version(info: Sequence[int]) -> str:
    major, minor, micro = int(info[0]), int(info[1]), int(info[2])
    return f"{major}.{minor}.{micro}"


def _env_is_set(environ: Mapping[str, str], name: str) -> bool:
    value = environ.get(name)
    return bool(value and value.strip())


def _config_has_api_key(config: Optional[Config]) -> bool:
    if config is None or config.api_key is None:
        return False
    secret = config.api_key.get_secret_value()
    return bool(secret and secret.strip())


def _classify_python(info: Sequence[int]) -> DoctorCheck:
    major, minor = int(info[0]), int(info[1])
    version = _format_python_version(info)
    if (major, minor) < (3, 10):
        return DoctorCheck(
            name="python",
            status=CheckStatus.FAIL,
            summary=f"Python {version} is not supported",
            detail="HolmesGPT requires Python 3.10 through 3.13.",
        )
    if (major, minor) >= (3, 14):
        return DoctorCheck(
            name="python",
            status=CheckStatus.WARN,
            summary=f"Python {version} is newer than the supported range",
            detail=(
                "HolmesGPT supports Python 3.10 through 3.13. "
                "Set DISABLE_PROMETHEUS_TOOLSET=true if Prometheus fails to import."
            ),
        )
    return DoctorCheck(
        name="python",
        status=CheckStatus.OK,
        summary=f"Python {version}",
    )


def _collect_llm_credentials(
    environ: Mapping[str, str], config: Optional[Config]
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if _config_has_api_key(config):
        entries.append({"name": "config.api_key", "set": True})
    for name in LLM_CREDENTIAL_ENV_VARS:
        entries.append({"name": name, "set": _env_is_set(environ, name)})
    return entries


def _probe_kubernetes(
    *,
    which_fn: Callable[[str], Optional[str]],
    run_command: Callable[..., subprocess.CompletedProcess[str]],
) -> Dict[str, Any]:
    kubectl = which_fn("kubectl")
    if not kubectl:
        return {
            "kubectl_found": False,
            "context": None,
            "error": "kubectl not found on PATH",
        }
    try:
        result = run_command(
            ["kubectl", "config", "current-context"],
            capture_output=True,
            text=True,
            timeout=_KUBECTL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "kubectl_found": True,
            "context": None,
            "error": f"kubectl timed out after {_KUBECTL_TIMEOUT_SECONDS:.0f}s",
        }
    except OSError as exc:
        return {
            "kubectl_found": True,
            "context": None,
            "error": str(exc),
        }

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode == 0 and stdout:
        return {"kubectl_found": True, "context": stdout, "error": None}
    return {
        "kubectl_found": True,
        "context": None,
        "error": stderr or stdout or f"kubectl exited {result.returncode}",
    }


def _summarize_toolsets(toolsets: Sequence[Toolset]) -> Dict[str, Any]:
    enabled = 0
    failed = 0
    unconfigured = 0
    disabled = 0
    failed_names: List[str] = []
    for toolset in toolsets:
        if toolset.status == ToolsetStatusEnum.ENABLED:
            enabled += 1
            continue
        if toolset.status == ToolsetStatusEnum.FAILED:
            if toolset.enabled:
                failed += 1
                if len(failed_names) < _MAX_FAILED_TOOLSET_NAMES:
                    failed_names.append(toolset.name)
            else:
                unconfigured += 1
            continue
        disabled += 1
    return {
        "enabled": enabled,
        "failed": failed,
        "unconfigured": unconfigured,
        "disabled": disabled,
        "failed_names": failed_names,
    }


def collect_doctor_report(
    *,
    config_file: Optional[Path] = None,
    skip_toolsets: bool = False,
    refresh_toolsets: bool = False,
    environ: Optional[Mapping[str, str]] = None,
    python_version: Optional[Sequence[int]] = None,
    get_version_fn: Callable[[], str] = get_version,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    load_config: Optional[Callable[[Optional[Path]], Config]] = None,
    list_toolsets: Optional[Callable[..., List[Toolset]]] = None,
) -> DoctorReport:
    """Build a doctor report. Dependencies are injectable for unit tests."""
    env = environ if environ is not None else os.environ
    if python_version is not None:
        py_info = (
            int(python_version[0]),
            int(python_version[1]),
            int(python_version[2]),
        )
    else:
        py_info = (
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        )
    config_path = (
        str(config_file) if config_file is not None else DEFAULT_CONFIG_LOCATION
    )
    config_exists = Path(config_path).exists()

    checks: List[DoctorCheck] = [
        DoctorCheck(
            name="version",
            status=CheckStatus.INFO,
            summary=get_version_fn(),
        ),
        _classify_python(py_info),
    ]

    if config_exists:
        checks.append(
            DoctorCheck(
                name="config",
                status=CheckStatus.OK,
                summary=config_path,
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="config",
                status=CheckStatus.WARN,
                summary=f"{config_path} not found",
                detail="Using defaults. Create this file to persist model and toolset settings.",
            )
        )

    config: Optional[Config] = None
    config_error: Optional[str] = None
    loader = load_config or Config.load_from_file
    try:
        config = loader(config_file)
    except Exception as exc:  # noqa: BLE001 - doctor must keep going
        config_error = str(exc)
        checks.append(
            DoctorCheck(
                name="config_load",
                status=CheckStatus.WARN,
                summary="Failed to load config",
                detail=config_error,
            )
        )

    credentials = _collect_llm_credentials(env, config)
    any_credentials = any(entry["set"] for entry in credentials)
    if any_credentials:
        set_names = [entry["name"] for entry in credentials if entry["set"]]
        checks.append(
            DoctorCheck(
                name="llm_credentials",
                status=CheckStatus.OK,
                summary="set: " + ", ".join(set_names),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="llm_credentials",
                status=CheckStatus.FAIL,
                summary="No LLM credentials found",
                detail=(
                    "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, AZURE_API_KEY, "
                    "GEMINI_API_KEY, or another supported provider key."
                ),
            )
        )

    if _env_is_set(env, "AWS_ACCESS_KEY_ID") and not _env_is_set(
        env, "AWS_SECRET_ACCESS_KEY"
    ):
        checks.append(
            DoctorCheck(
                name="aws_credentials",
                status=CheckStatus.WARN,
                summary="AWS_ACCESS_KEY_ID is set without AWS_SECRET_ACCESS_KEY",
            )
        )

    creds_path = env.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if creds_path and not Path(creds_path).is_file():
        checks.append(
            DoctorCheck(
                name="google_credentials_file",
                status=CheckStatus.WARN,
                summary="GOOGLE_APPLICATION_CREDENTIALS does not point to a file",
            )
        )

    model_name = config.model if config and config.model else DEFAULT_MODEL
    model_source: Optional[str]
    if config and config.model:
        model_source = getattr(config, "_model_source", None) or "config"
    else:
        model_source = "default"
        checks.append(
            DoctorCheck(
                name="model",
                status=CheckStatus.INFO,
                summary=f"Using default model {DEFAULT_MODEL}",
                detail="Set model in the config file or the MODEL environment variable.",
            )
        )

    kubernetes = _probe_kubernetes(which_fn=which_fn, run_command=run_command)
    if kubernetes.get("context"):
        checks.append(
            DoctorCheck(
                name="kubernetes",
                status=CheckStatus.OK,
                summary=f"context {kubernetes['context']}",
            )
        )
    elif not kubernetes.get("kubectl_found"):
        checks.append(
            DoctorCheck(
                name="kubernetes",
                status=CheckStatus.WARN,
                summary="kubectl not found",
                detail="Optional. Install kubectl if you investigate Kubernetes clusters.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="kubernetes",
                status=CheckStatus.WARN,
                summary="kubectl found, no current context",
                detail=kubernetes.get("error"),
            )
        )

    checks.append(
        DoctorCheck(
            name="tool_memory_limit",
            status=CheckStatus.INFO,
            summary=f"TOOL_MEMORY_LIMIT_MB={TOOL_MEMORY_LIMIT_MB}",
        )
    )

    toolsets_summary: Optional[Dict[str, Any]] = None
    if not skip_toolsets and config is not None:
        try:
            lister = list_toolsets or config.toolset_manager.list_console_toolsets
            toolsets = lister(refresh_status=refresh_toolsets)
            toolsets_summary = _summarize_toolsets(toolsets)
            failed = int(toolsets_summary["failed"])
            enabled = int(toolsets_summary["enabled"])
            summary = (
                f"{enabled} enabled, {failed} failed, "
                f"{toolsets_summary['unconfigured']} unconfigured, "
                f"{toolsets_summary['disabled']} disabled"
            )
            detail: Optional[str] = None
            if enabled == 0:
                status = CheckStatus.WARN
                detail = (
                    "No toolsets are enabled. Run `holmes toolset list` for details."
                )
            elif failed:
                status = CheckStatus.WARN
                failed_names = toolsets_summary["failed_names"]
                if failed_names:
                    detail = "Failed: " + ", ".join(failed_names)
            else:
                status = CheckStatus.OK
            checks.append(
                DoctorCheck(
                    name="toolsets",
                    status=status,
                    summary=summary,
                    detail=detail,
                )
            )
        except Exception as exc:  # noqa: BLE001 - doctor must keep going
            checks.append(
                DoctorCheck(
                    name="toolsets",
                    status=CheckStatus.WARN,
                    summary="Failed to load toolset status",
                    detail=str(exc),
                )
            )

    return DoctorReport(
        version=get_version_fn(),
        python_version=_format_python_version(py_info),
        config_path=config_path,
        config_exists=config_exists,
        model=model_name,
        model_source=model_source,
        llm_credentials=credentials,
        kubernetes=kubernetes,
        tool_memory_limit_mb=TOOL_MEMORY_LIMIT_MB,
        toolsets=toolsets_summary,
        checks=checks,
    )


_STATUS_STYLE = {
    CheckStatus.OK: ("green", "ok"),
    CheckStatus.WARN: ("yellow", "warn"),
    CheckStatus.FAIL: ("red", "fail"),
    CheckStatus.INFO: ("cyan", "info"),
}


def render_doctor_report(report: DoctorReport, console: Console) -> None:
    """Pretty-print a doctor report. Never prints credential values."""
    table = Table(title="HolmesGPT doctor", show_header=True, header_style="bold")
    table.add_column("Status", style="bold", width=6)
    table.add_column("Check", style="bold")
    table.add_column("Summary")

    for check in report.checks:
        color, label = _STATUS_STYLE[check.status]
        summary = check.summary
        if check.detail:
            summary = f"{summary}\n[dim]{check.detail}[/dim]"
        table.add_row(f"[{color}]{label}[/{color}]", check.name, summary)

    console.print(table)
    if report.ok:
        failed = sum(1 for check in report.checks if check.status == CheckStatus.FAIL)
        warnings = sum(1 for check in report.checks if check.status == CheckStatus.WARN)
        if warnings:
            console.print(
                f"[yellow]Doctor finished with {warnings} warning(s). "
                "Investigations can still run.[/yellow]"
            )
        else:
            console.print("[green]Doctor finished: no failed checks.[/green]")
        _ = failed
    else:
        failed = sum(1 for check in report.checks if check.status == CheckStatus.FAIL)
        console.print(
            f"[red]Doctor found {failed} failed check(s). "
            "Fix these before running investigations.[/red]"
        )


def dumps_doctor_report(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), indent=2, default=str) + "\n"
