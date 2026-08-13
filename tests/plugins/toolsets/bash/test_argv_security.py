"""Regression tests for the argv-level security checks in the bash toolset.

Prefix allow-listing validates only a command's *name*. Some allow-listed
commands accept arguments (or shell redirections) that turn a read-only tool
into arbitrary code execution, file writes, or deletion. `validate_command`
must DENY those regardless of allow-list membership.

The MUST_ALLOW set is a small, hand-curated and sanitized sample representative
of real HolmesGPT bash traffic (kubectl-heavy read-only troubleshooting, piped
text filters). It guards against false positives. The full production corpus is
kept private (it can contain secrets/hostnames); see the security ticket for the
query that regenerates it and how to replay this check over it.
"""

import pytest

from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.default_lists import EXTENDED_ALLOW_LIST
from holmes.plugins.toolsets.bash.validation import (
    DenyReason,
    ValidationStatus,
    _uniq_positional_args,
    get_effective_lists,
    validate_command,
)

# Effective lists for the Helm default tier (extended), where find/cat/etc. live.
_ALLOW, _DENY = get_effective_lists(BashExecutorConfig(builtin_allowlist="extended"))


def _validate(command: str, prefixes):
    return validate_command(command, prefixes, _ALLOW, _DENY)


# ---------------------------------------------------------------------------
# MUST DENY — code-exec / write / delete primitives that prefix matching misses.
# ---------------------------------------------------------------------------
MUST_DENY = [
    # find action primitives -> command execution
    ('find . -exec sh -c "id" \\;', ["find"]),
    ("find . -execdir cat {} \\;", ["find"]),
    ("find / -ok rm {} \\;", ["find"]),
    ("find / -okdir rm {} \\;", ["find"]),
    # find action primitives -> write / delete
    ("find . -name '*.log' -delete", ["find"]),
    ("find . -fprintf /tmp/out %p", ["find"]),
    ("find . -fprint /tmp/out", ["find"]),
    ("find . -fprint0 /tmp/out", ["find"]),  # null-separated variant of -fprint
    ("find . -fls /tmp/out", ["find"]),
    # sort -> exec on spill / arbitrary write
    ("sort --compress-program=/tmp/x big.txt", ["sort"]),
    ("sort --compress-program /tmp/x big.txt", ["sort"]),
    ("sort -o /tmp/out in.txt", ["sort"]),
    ("sort -o/tmp/out in.txt", ["sort"]),
    ("sort --output=/tmp/out in.txt", ["sort"]),
    # sort -o hidden inside a short-option cluster (`-ro` == `-r -o`)
    ("sort -ro /tmp/out in.txt", ["sort"]),
    ("sort -rofile in.txt", ["sort"]),
    ("sort -uo /tmp/out in.txt", ["sort"]),
    # GNU long-option abbreviations must not slip past the check
    ("sort --out /tmp/out in.txt", ["sort"]),
    ("sort --o /tmp/out in.txt", ["sort"]),
    ("sort --compress-prog=/tmp/evil in.txt", ["sort"]),
    ("sort --comp /tmp/evil in.txt", ["sort"]),
    # a hard deny must win even when an earlier segment has a shell expansion
    ("sort $OPTS in.txt | find . -delete", ["sort", "find"]),
    ("sort $OPTS in.txt > /tmp/pwn", ["sort"]),
    # uniq second positional -> arbitrary write
    ("uniq in.txt /tmp/out", ["uniq"]),
    ("uniq /etc/passwd /tmp/out", ["uniq"]),
    # output redirection to a real file -> arbitrary write (any command)
    ("echo pwned > /tmp/pwn", ["echo"]),
    ("echo pwned >> /tmp/pwn", ["echo"]),
    ("kubectl get pods -o yaml > /tmp/pods.yaml", ["kubectl get"]),
    ("cat /etc/hosts > /tmp/copy", ["cat"]),
    # redirect fires even when combined with an otherwise-approval command
    ("mkdir -p /tmp/x && echo hi > /tmp/x/f", ["mkdir", "echo"]),
]

# ---------------------------------------------------------------------------
# MUST ALLOW — representative, sanitized real-world read-only commands.
# ---------------------------------------------------------------------------
MUST_ALLOW = [
    ("kubectl get pods -n default -o wide", ["kubectl get"]),
    (
        "kubectl get pods -A -o custom-columns=NAME:.metadata.name,STATUS:.status.phase",
        ["kubectl get"],
    ),
    ("kubectl describe pod api-server-1 -n default", ["kubectl describe"]),
    ("kubectl get ns -o name", ["kubectl get"]),
    ("kubectl top pods -n default", ["kubectl top"]),
    ("kubectl get events -n default --sort-by=.lastTimestamp", ["kubectl get"]),
    # benign redirections: fd-dup and /dev/null must NOT be treated as writes
    ("kubectl get pods -o wide 2>/dev/null", ["kubectl get"]),
    (
        "kubectl logs api-server-1 -n default --tail=50 2>&1 | tail -25",
        ["kubectl logs", "tail"],
    ),
    ("kubectl get svc -A --no-headers | grep -Ei 'LoadBalancer|NodePort'", ["kubectl get", "grep"]),
    ("kubectl get pods -o yaml | grep -i image", ["kubectl get", "grep"]),
    ('echo "=== deployments ==="; kubectl get deploy -n default', ["echo", "kubectl get"]),
    # piped text filters — the dominant real-world pattern
    (
        "cat /tmp/.holmes/abc/tool_results/x.json | jq -r '.[].log' | sort | uniq -c",
        ["cat", "jq", "sort", "uniq"],
    ),
    ("jq -r '.items[].metadata.name'", ["jq"]),
    ("sort -k2 -rn", ["sort"]),
    ("sort -to input.txt", ["sort"]),  # -t's separator is 'o', NOT sort -o
    ("grep -o foo", ["grep"]),  # grep's -o must not trip the sort -o rule
    ("wc -l", ["wc"]),
    ("cut -d: -f1", ["cut"]),
    ("tr -d '\\n'", ["tr"]),
    # read-only filesystem inspection (extended tier)
    ("find . -name '*.yaml' -type f", ["find"]),
    ("ls -la /var/log", ["ls"]),
    ("df -h", ["df"]),
    # a *quoted* literal that merely contains `$(`/backtick is inert -> allowed
    ("find . -name '*$(x)*'", ["find"]),
    ("find . -name '*.log' -type f", ["find"]),
    # env vars in a non-argv-checked command keep working (auto-approved feature)
    ("kubectl get pods -n $NS -o wide", ["kubectl get"]),
    # uniq value-taking options must not be mistaken for an output positional
    ("uniq -c", ["uniq"]),
    ("uniq -f 2 input.txt", ["uniq"]),
    ("uniq -cf 2 input.txt", ["uniq"]),  # -f's value inside a cluster (-cf)
    ("uniq --skip-fields 2 input.txt", ["uniq"]),
    ("uniq --skip-f 2 input.txt", ["uniq"]),  # abbreviated long option + its value
    ("uniq --check-c 3 input.txt", ["uniq"]),
    ("uniq input.txt -", ["uniq"]),  # '-' 2nd positional = stdout, not a file write
    # benign device redirect targets are not treated as writes
    ("kubectl logs api-server-1 -n default > /dev/null", ["kubectl logs"]),
    ("kubectl get pods 2>&1 | head", ["kubectl get", "head"]),
]


@pytest.mark.parametrize("command,prefixes", MUST_DENY, ids=[c for c, _ in MUST_DENY])
def test_dangerous_commands_denied(command, prefixes):
    result = _validate(command, prefixes)
    assert result.status == ValidationStatus.DENIED, (
        f"expected DENIED for {command!r}, got {result.status} ({result.message})"
    )
    assert result.deny_reason == DenyReason.DANGEROUS_ARGUMENT


@pytest.mark.parametrize("command,prefixes", MUST_ALLOW, ids=[c for c, _ in MUST_ALLOW])
def test_benign_commands_allowed(command, prefixes):
    result = _validate(command, prefixes)
    assert result.status == ValidationStatus.ALLOWED, (
        f"expected ALLOWED for {command!r}, got {result.status} ({result.message})"
    )


class TestRemovedFromAllowlist:
    """tar/gzip/zcat/zgrep were removed from the builtin extended list; they must
    no longer auto-execute (they fall through to approval)."""

    @pytest.mark.parametrize(
        "command,prefixes",
        [
            ("tar -tf archive.tar", ["tar -tf"]),
            ("tar -tvf archive.tar", ["tar -tvf"]),
            ("gzip -l file.gz", ["gzip -l"]),
            ("zcat file.gz", ["zcat"]),
            ("zgrep pattern file.gz", ["zgrep"]),
        ],
    )
    def test_archive_tools_require_approval(self, command, prefixes):
        result = _validate(command, prefixes)
        assert result.status == ValidationStatus.APPROVAL_REQUIRED

    def test_removed_from_extended_list(self):
        for removed in ("tar -tf", "tar -tvf", "gzip -l", "zcat", "zgrep"):
            assert removed not in EXTENDED_ALLOW_LIST


class TestRedirectTargets:
    """Only writes to a real file are denied; standard streams / devices are not."""

    @pytest.mark.parametrize(
        "command,prefixes",
        [
            ("echo hi > /tmp/f", ["echo"]),
            ("echo hi >> /tmp/f", ["echo"]),
            # capturing stderr to a real file is still a filesystem write (strict)
            ("kubectl get pods 2>/tmp/err", ["kubectl get"]),
        ],
    )
    def test_real_file_redirect_denied(self, command, prefixes):
        assert _validate(command, prefixes).status == ValidationStatus.DENIED

    @pytest.mark.parametrize(
        "command,prefixes",
        [
            ("kubectl get pods 2>/dev/null", ["kubectl get"]),
            ("kubectl get pods > /dev/null 2>&1", ["kubectl get"]),
            ("kubectl get pods 2>&1", ["kubectl get"]),
            ("echo hi > /dev/tty", ["echo"]),
        ],
    )
    def test_benign_redirect_allowed(self, command, prefixes):
        assert _validate(command, prefixes).status == ValidationStatus.ALLOWED


class TestCommandSubstitutionGuard:
    """Command substitution can expand into a blocked primitive at runtime; for the
    argv-checked commands we cannot verify it, so it must not auto-execute."""

    @pytest.mark.parametrize(
        "command,prefixes",
        [
            ("find . $(echo -delete)", ["find"]),
            ("find . `echo -delete`", ["find"]),
            ("sort $(echo -o) out.txt", ["sort"]),
            ("sort `echo -o` out.txt", ["sort"]),
            # single opaque token that could expand to `input.txt output.txt`
            ("uniq $(echo input.txt)", ["uniq"]),
            ("find /logs/$(date +%F) -name '*.log'", ["find"]),  # even benign intent
            # parameter expansion can also reconstruct a primitive at runtime
            ("find . -${Z}exec sh -c id \\;", ["find"]),  # unset Z -> `-exec`
            ("sort $OPTS in.txt", ["sort"]),  # OPTS could word-split to `-o FILE`
            ("uniq $ARGS", ["uniq"]),  # ARGS could word-split to `in out`
            # process substitution runs a command
            ("sort <(echo hi)", ["sort"]),
        ],
    )
    def test_substitution_in_checked_command_requires_approval(self, command, prefixes):
        result = _validate(command, prefixes)
        assert result.status == ValidationStatus.APPROVAL_REQUIRED, (
            f"expected APPROVAL_REQUIRED for {command!r}, got {result.status}"
        )

    def test_substitution_in_other_command_still_allowed(self):
        # echo/kubectl are not argv-checked; existing substitution behaviour is kept.
        assert _validate("echo $(whoami)", ["echo"]).status == ValidationStatus.ALLOWED


class TestUniqPositionalParsing:
    """The uniq output-file check must count positionals correctly, skipping the
    values consumed by -f/-s/-w and the long forms."""

    @pytest.mark.parametrize(
        "args,expected",
        [
            ([], []),
            (["-c"], []),
            (["input.txt"], ["input.txt"]),
            (["input.txt", "output.txt"], ["input.txt", "output.txt"]),
            (["-f", "2", "input.txt"], ["input.txt"]),  # '2' is -f's value
            (["-f2", "input.txt"], ["input.txt"]),  # joined form
            (["-cf", "2", "input.txt"], ["input.txt"]),  # -f's value inside a cluster
            (["-c", "input.txt", "output.txt"], ["input.txt", "output.txt"]),
            (["--skip-fields", "2", "input.txt"], ["input.txt"]),
            (["--skip-fields=2", "input.txt"], ["input.txt"]),
            (["-", "output.txt"], ["-", "output.txt"]),  # '-' (stdin) is positional
            (["--", "-weird-name", "out"], ["-weird-name", "out"]),
        ],
    )
    def test_uniq_positional_args(self, args, expected):
        assert _uniq_positional_args(args) == expected
