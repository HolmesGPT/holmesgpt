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
from holmes.plugins.toolsets.bash.common.default_lists import (
    CORE_ALLOW_LIST,
    EXTENDED_ALLOW_LIST,
)
from holmes.plugins.toolsets.bash.command_arg_rules import _uniq_positional_args
from holmes.plugins.toolsets.bash.validation import (
    DenyReason,
    ValidationStatus,
    get_effective_lists,
    validate_command,
)

# Effective lists for the Helm default tier (extended), where find/cat/etc. live.
_ALLOW, _DENY = get_effective_lists(BashExecutorConfig(builtin_allowlist="extended"))


@pytest.fixture(autouse=True)
def _default_deny_env(monkeypatch):
    """Most tests assert the default (deny) behavior; ensure the mode env var is
    unset so an ambient HOLMES_BASH_UNSAFE_ARGS_MODE cannot mask a regression.
    Tests that exercise approval mode set it explicitly and override this."""
    monkeypatch.delenv("HOLMES_BASH_UNSAFE_ARGS_MODE", raising=False)


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


class TestDenyBeatsArgvApproval:
    """A hardcoded-block / deny-list DENY on any segment must win over an argv
    approval (the shell-expansion gate, or an exec/write vector in approval mode)
    — the argv check must not short-circuit before segments are validated."""

    @pytest.mark.parametrize(
        "command,prefixes",
        [
            # sudo (hardcoded block) alongside a find with a shell-expansion arg
            ("find . $(echo x); sudo id", ["find", "sudo"]),
            ("sudo id; find . $(echo x)", ["sudo", "find"]),
            ("find . $(echo x) && sudo reboot", ["find", "sudo"]),
        ],
    )
    def test_hardcoded_block_beats_shell_expansion_approval(self, command, prefixes):
        result = _validate(command, prefixes)
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_hardcoded_block_beats_approval_mode_vector(self, monkeypatch):
        # Even in approval mode, a sudo segment must DENY, not become approvable.
        monkeypatch.setenv("HOLMES_BASH_UNSAFE_ARGS_MODE", "approval")
        result = _validate("echo hi > /tmp/f; sudo id", ["echo", "sudo"])
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_legit_shell_expansion_still_requires_approval(self):
        # Regression guard: without a denied segment, the gate still applies.
        result = _validate("find . $(echo '*.log')", ["find"])
        assert result.status == ValidationStatus.APPROVAL_REQUIRED


class TestUnsafeArgsMode:
    """HOLMES_BASH_UNSAFE_ARGS_MODE selects deny (default) vs approval for the
    exec/write vectors. Neither mode auto-executes."""

    @pytest.mark.parametrize(
        "command,prefixes",
        [
            ("find . -exec grep ERROR {} \\;", ["find"]),
            ("sort -o /tmp/out in.txt", ["sort"]),
            ("echo hi > /tmp/f", ["echo"]),
        ],
    )
    def test_approval_mode_relaxes_deny_to_approval(self, command, prefixes, monkeypatch):
        monkeypatch.setenv("HOLMES_BASH_UNSAFE_ARGS_MODE", "approval")
        assert _validate(command, prefixes).status == ValidationStatus.APPROVAL_REQUIRED

    def test_default_is_hard_deny(self, monkeypatch):
        monkeypatch.delenv("HOLMES_BASH_UNSAFE_ARGS_MODE", raising=False)
        assert _validate("echo hi > /tmp/f", ["echo"]).status == ValidationStatus.DENIED

    def test_unknown_value_fails_safe_to_deny(self, monkeypatch):
        monkeypatch.setenv("HOLMES_BASH_UNSAFE_ARGS_MODE", "yolo")
        assert _validate("echo hi > /tmp/f", ["echo"]).status == ValidationStatus.DENIED


class TestAllowListGuard:
    """Trip-wire: any change to the builtin allow lists must be a deliberate act.

    A new command can introduce argv-level write/exec vectors the checks in
    validation.py don't yet cover. If this test fails because you changed an
    allow list, review that command's dangerous arguments (see
    checkers in command_arg_rules.py) and add coverage, THEN update the expected set below.

    (For example, tar/gzip/zcat/zgrep are intentionally absent: they carry
    argument-level code-execution or argument-injection risk and were unused in
    practice; add them to the `allow` config if a deployment needs them.)
    """

    EXPECTED_CORE = frozenset({
        "kubectl get", "kubectl describe", "kubectl logs", "kubectl top",
        "kubectl explain", "kubectl api-resources", "kubectl config view",
        "kubectl config current-context", "kubectl cluster-info", "kubectl version",
        "kubectl auth can-i", "kubectl diff", "kubectl events",
        "jq", "grep", "head", "tail", "sort", "uniq", "wc", "cut", "tr",
        "id", "whoami", "hostname", "uname", "date", "which", "type", "echo",
    })
    EXPECTED_EXTENDED_ONLY = frozenset({"cat", "base64", "ls", "find", "stat", "du", "df"})

    def test_core_allow_list_unchanged(self):
        assert set(CORE_ALLOW_LIST) == self.EXPECTED_CORE

    def test_extended_only_additions_unchanged(self):
        extended_only = set(EXTENDED_ALLOW_LIST) - set(CORE_ALLOW_LIST)
        assert extended_only == self.EXPECTED_EXTENDED_ONLY


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
