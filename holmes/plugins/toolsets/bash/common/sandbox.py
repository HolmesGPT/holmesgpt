"""
Bubblewrap (bwrap) based sandboxing for bash tool execution.

This module wraps an approved bash command in an unprivileged OS-level jail so
that the command cannot read the host's credentials, environment, or filesystem.
It is an opt-in proof of concept; see docs/design/2026-06-05_bash-sandboxing.md
for the full design and the list of follow-ups.

Key properties of the jail:
- Fresh user/pid/ipc/uts/cgroup namespaces (no privilege required).
- A scrubbed environment: the env is cleared and only an allowlist is re-injected.
- A curated read-only system root; the host's home, config, and secret dirs are
  NOT mounted.
- Ephemeral tmpfs for /tmp and the working directory.
- The host network is intentionally shared (Holmes must reach K8s/Prometheus/etc).
"""

import functools
import logging
import os
import shutil
import subprocess
from typing import List, Optional

from holmes.common.env_vars import (
    HOLMES_BASH_SANDBOX_ENABLED,
    HOLMES_BASH_SANDBOX_ENV_PASSTHROUGH,
)

logger = logging.getLogger(__name__)

BWRAP_BINARY = "bwrap"

# System directories bind-mounted read-only into the jail. Only those that exist
# on the host are bound. Deliberately excludes /etc as a whole (it may hold host
# secrets); specific /etc files needed for DNS/TLS are bound separately below.
_RO_SYSTEM_DIRS = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32"]

# Specific /etc files needed for name resolution and TLS verification.
_RO_ETC_FILES = [
    "/etc/resolv.conf",
    "/etc/ssl",
    "/etc/ca-certificates",
    "/etc/ca-certificates.conf",
    "/etc/hosts",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/group",
]

# Environment variables always carried into the jail. Intentionally minimal and
# credential-free. Anything else must be opted in via
# HOLMES_BASH_SANDBOX_ENV_PASSTHROUGH.
_DEFAULT_ENV_ALLOWLIST = ["PATH", "HOME", "LANG", "TERM", "TZ"]
_DEFAULT_ENV_PREFIXES = ["LC_"]

# Working directory (and HOME) inside the jail. Backed by tmpfs, so ephemeral.
_JAIL_WORKDIR = "/work"


def sandbox_enabled() -> bool:
    """Whether bash sandboxing has been opted into via env var."""
    return bool(HOLMES_BASH_SANDBOX_ENABLED)


@functools.lru_cache(maxsize=1)
def is_sandbox_available() -> bool:
    """
    Runtime probe for whether the bwrap jail actually works here.

    Checks that the bwrap binary is present AND that a trivial jailed command
    can be created (this exercises unprivileged user-namespace creation, which
    some hardened kernels/containers disable). The result is cached.
    """
    if shutil.which(BWRAP_BINARY) is None:
        logger.warning(
            "Bash sandboxing requested but '%s' is not on PATH; "
            "falling back to direct execution.",
            BWRAP_BINARY,
        )
        return False

    try:
        # Probe with the real jail layout so the check matches actual execution
        # (in particular it exercises unprivileged user-namespace creation and
        # confirms the bind set is sufficient to exec bash).
        probe = subprocess.run(
            build_sandbox_argv("exit 0"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(
            "Bash sandboxing requested but the bwrap probe failed (%s); "
            "falling back to direct execution.",
            e,
        )
        return False

    if probe.returncode != 0:
        logger.warning(
            "Bash sandboxing requested but the bwrap probe exited %s "
            "(unprivileged user namespaces may be disabled): %s; "
            "falling back to direct execution.",
            probe.returncode,
            probe.stderr.decode(errors="replace").strip(),
        )
        return False

    return True


def should_sandbox() -> bool:
    """True if sandboxing is both opted into and actually usable here."""
    return sandbox_enabled() and is_sandbox_available()


def _env_allowlist() -> List[str]:
    extra = [
        name.strip()
        for name in HOLMES_BASH_SANDBOX_ENV_PASSTHROUGH.split(",")
        if name.strip()
    ]
    return _DEFAULT_ENV_ALLOWLIST + extra


def _setenv_args() -> List[str]:
    """Build --setenv args for the allowlisted environment variables that are set."""
    args: List[str] = []
    allowlist = _env_allowlist()
    for name, value in os.environ.items():
        if name in allowlist or any(name.startswith(p) for p in _DEFAULT_ENV_PREFIXES):
            args += ["--setenv", name, value]
    # Ensure HOME points at the writable jail workdir even if not set on the host.
    if "HOME" not in os.environ:
        args += ["--setenv", "HOME", _JAIL_WORKDIR]
    # Guarantee a sane PATH inside the jail.
    if "PATH" not in os.environ:
        args += ["--setenv", "PATH", "/usr/bin:/bin:/usr/sbin:/sbin"]
    return args


def build_sandbox_argv(
    inner_cmd: str, rw_bind_paths: Optional[List[str]] = None
) -> List[str]:
    """
    Build the full bwrap argv that runs ``inner_cmd`` via /bin/bash inside the jail.

    ``inner_cmd`` is the already-prepared shell string (e.g. the ulimit-prefixed
    command). It is passed to ``bash -c`` inside the jail, so normal shell
    semantics (pipes, &&, etc.) still apply.

    ``rw_bind_paths`` is an optional list of host directories to bind-mount
    read-write at the same absolute path inside the jail. This is how the
    per-session tool-result spill directory is made visible so the LLM can
    ``cat``/``grep`` large results that the framework wrote to disk. These paths
    are session-scoped on purpose — the whole spill base is never bound, so one
    session's jail cannot see another session's spilled files.
    """
    argv: List[str] = [
        BWRAP_BINARY,
        # Isolation: fresh namespaces. Network is intentionally NOT unshared.
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--die-with-parent",
        "--new-session",
        # Scrub the environment, then re-inject only the allowlist.
        "--clearenv",
    ]
    argv += _setenv_args()

    # Read-only system root (only what exists).
    for path in _RO_SYSTEM_DIRS:
        if os.path.exists(path):
            argv += ["--ro-bind", path, path]
    for path in _RO_ETC_FILES:
        if os.path.exists(path):
            argv += ["--ro-bind", path, path]

    # Kernel/dev filesystems and ephemeral writable space.
    argv += [
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", _JAIL_WORKDIR,
        "--chdir", _JAIL_WORKDIR,
    ]

    # Session-scoped read-write binds (e.g. the tool-result spill dir). These come
    # AFTER the tmpfs mounts so that a bind under /tmp layers on top of the tmpfs
    # rather than being hidden by it (bwrap applies operations in argv order).
    for path in rw_bind_paths or []:
        if path and os.path.isdir(path):
            argv += ["--bind", path, path]

    # The command itself.
    argv += ["/bin/bash", "-c", inner_cmd]
    return argv
