"""
Unit tests for the bash toolset validation module.

Tests prefix-based command validation, subshell detection, and allow/deny list handling.
"""

from unittest.mock import MagicMock

import pytest

from holmes.plugins.toolsets.bash.bash_toolset import BashExecutorToolset, RunBashCommand
from holmes.plugins.toolsets.bash.common.config import (
    HARDCODED_BLOCKS,
    BashExecutorConfig,
)
from holmes.plugins.toolsets.bash.common.default_lists import (
    CORE_ALLOW_LIST,
    DEFAULT_DENY_LIST,
    EXTENDED_ALLOW_LIST,
)
from holmes.plugins.toolsets.bash.validation import (
    DenyReason,
    ValidationStatus,
    check_blocked_in_raw_command,
    check_hardcoded_blocks,
    get_effective_lists,
    match_prefix,
    match_prefix_for_deny,
    parse_command_segments,
    validate_command,
    validate_segment,
)


class TestMatchPrefix:
    """Tests for the prefix matching logic."""

    def test_exact_match(self):
        """Test that exact matches work."""
        assert match_prefix("kubectl", "kubectl")
        assert match_prefix("grep", "grep")

    def test_prefix_match_with_args(self):
        """Test that prefix matches work with additional arguments."""
        assert match_prefix("kubectl get pods", "kubectl get")
        assert match_prefix("grep -r error", "grep")
        assert match_prefix("kubectl get pods -n default", "kubectl get")

    def test_prefix_with_subcommand(self):
        """Test that subcommand prefixes work."""
        assert match_prefix("kubectl get pods", "kubectl get")
        assert match_prefix("kubectl describe pod my-pod", "kubectl describe")

    def test_no_match_different_command(self):
        """Test that different commands don't match."""
        assert not match_prefix("kubectl delete pod", "kubectl get")
        assert not match_prefix("grep error", "cat")

    def test_no_partial_word_match(self):
        """Test that partial word matches are rejected."""
        # 'kubectlx' should not match 'kubectl'
        assert not match_prefix("kubectlx get", "kubectl")
        # 'greps' should not match 'grep'
        assert not match_prefix("greps error", "grep")

    def test_path_separator_boundary(self):
        """Test that '/' is treated as a valid boundary."""
        assert match_prefix("kubectl get secret/my-secret", "kubectl get secret")
        assert match_prefix("cat /etc/passwd", "cat")

    def test_whitespace_handling(self):
        """Test that whitespace is handled correctly."""
        assert match_prefix("  kubectl get pods  ", "kubectl get")
        assert match_prefix("kubectl get pods", "  kubectl get  ")


class TestMatchPrefixForDeny:
    """Tests for the stricter deny list prefix matching."""

    def test_exact_match(self):
        """Test that exact matches work."""
        assert match_prefix_for_deny("kubectl get secret", "kubectl get secret")

    def test_word_boundary_match(self):
        """Test standard word boundary matching (space)."""
        assert match_prefix_for_deny(
            "kubectl get secret my-secret", "kubectl get secret"
        )
        assert match_prefix_for_deny("kubectl get secret -o yaml", "kubectl get secret")

    def test_path_separator_boundary(self):
        """Test that '/' is treated as a valid boundary for deny matching."""
        assert match_prefix_for_deny(
            "kubectl get secret/my-secret", "kubectl get secret"
        )
        assert match_prefix_for_deny("kubectl get secret/foo/bar", "kubectl get secret")

    def test_plural_form_auto_match(self):
        """Test that plural forms are automatically matched."""
        # 'secrets' should match deny prefix 'secret'
        assert match_prefix_for_deny("kubectl get secrets", "kubectl get secret")
        assert match_prefix_for_deny(
            "kubectl get secrets -n default", "kubectl get secret"
        )
        assert match_prefix_for_deny(
            "kubectl get secrets/my-secret", "kubectl get secret"
        )

    def test_no_match_different_command(self):
        """Test that unrelated commands don't match."""
        assert not match_prefix_for_deny("kubectl get pods", "kubectl get secret")
        assert not match_prefix_for_deny("kubectl get configmaps", "kubectl get secret")

    def test_no_partial_word_match(self):
        """Test that random continuations don't match (not just 's' for plural)."""
        # 'secretstore' should not match 'secret' (not a plural, not a boundary)
        assert not match_prefix_for_deny(
            "kubectl get secretstore", "kubectl get secret"
        )
        # But 'secretstores' should match (plural of secretstore... wait no)
        # Actually 'secretstores' starts with 'secrets' which is prefix+'s', so it would match
        # Let's test a clearer case
        assert not match_prefix_for_deny("kubectl get secretfoo", "kubectl get secret")


class TestParseCommandSegments:
    """Tests for command segment parsing."""

    def test_simple_command(self):
        """Test parsing a simple command."""
        segments, has_compound = parse_command_segments("kubectl get pods")
        assert segments == ["kubectl get pods"]
        assert not has_compound

    def test_piped_command(self):
        """Test parsing a piped command."""
        segments, has_compound = parse_command_segments("kubectl get pods | grep error")
        assert segments == ["kubectl get pods", "grep error"]
        assert not has_compound

    def test_multiple_pipes(self):
        """Test parsing multiple pipes."""
        segments, has_compound = parse_command_segments("kubectl get pods | grep error | head -10")
        assert segments == ["kubectl get pods", "grep error", "head -10"]
        assert not has_compound

    def test_and_operator(self):
        """Test parsing && operator."""
        segments, has_compound = parse_command_segments("mkdir test && cd test")
        assert segments == ["mkdir test", "cd test"]
        assert not has_compound

    def test_or_operator(self):
        """Test parsing || operator."""
        segments, has_compound = parse_command_segments("test -f file.txt || touch file.txt")
        assert segments == ["test -f file.txt", "touch file.txt"]
        assert not has_compound

    def test_semicolon_operator(self):
        """Test parsing ; operator."""
        segments, has_compound = parse_command_segments("echo hello; echo world")
        assert segments == ["echo hello", "echo world"]
        assert not has_compound

    def test_background_operator(self):
        """Test parsing & operator."""
        segments, has_compound = parse_command_segments("sleep 10 & echo done")
        assert segments == ["sleep 10", "echo done"]
        assert not has_compound

    def test_invalid_pipe_syntax_raises(self):
        """Test that invalid pipe syntax raises a parsing error."""
        import bashlex

        with pytest.raises(bashlex.errors.ParsingError):
            parse_command_segments("  |  kubectl get pods  |  ")

    def test_for_loop_extracts_inner_segments(self):
        """For loop returns inner command segments with compound flag."""
        segments, has_compound = parse_command_segments('for i in 1 2 3; do echo "$i"; done')
        assert has_compound
        assert len(segments) > 0
        assert any("echo" in s for s in segments)

    def test_if_statement_extracts_inner_segments(self):
        """If statement returns inner command segments with compound flag."""
        segments, has_compound = parse_command_segments("if [ -f file ]; then cat file; fi")
        assert has_compound
        assert len(segments) > 0

    def test_case_statement_raises(self):
        """Case statement (unsupported by bashlex) raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            parse_command_segments("case $x in 1) echo one;; 2) echo two;; esac")


class TestCheckHardcodedBlocks:
    """Tests for hardcoded block detection."""

    def test_sudo_blocked(self):
        """Test that sudo is blocked."""
        assert check_hardcoded_blocks("sudo apt-get install") == "sudo"
        assert check_hardcoded_blocks("sudo ls") == "sudo"

    def test_su_blocked(self):
        """Test that su is blocked."""
        assert check_hardcoded_blocks("su - root") == "su"
        assert check_hardcoded_blocks("su root -c 'whoami'") == "su"

    def test_normal_commands_not_blocked(self):
        """Test that normal commands are not blocked."""
        assert check_hardcoded_blocks("kubectl get pods") is None
        assert check_hardcoded_blocks("grep error log.txt") is None
        assert check_hardcoded_blocks("ls -la") is None

    def test_case_insensitive(self):
        """Test that blocking is case-insensitive."""
        assert check_hardcoded_blocks("SUDO apt-get install") == "sudo"

    def test_no_false_positives_from_substring(self):
        """Test that commands containing 'su' as substring are NOT blocked."""
        # These should NOT be blocked - 'su' appears as substring, not command
        assert check_hardcoded_blocks("echo issue") is None
        assert check_hardcoded_blocks("echo result") is None
        assert check_hardcoded_blocks("sum 1 2 3") is None
        assert check_hardcoded_blocks("sudo_wrapper ls") is None  # not a word boundary
        # But these SHOULD be blocked - 'su' is the actual command
        assert check_hardcoded_blocks("su") == "su"
        assert check_hardcoded_blocks("su -") == "su"


class TestCheckBlockedInRawCommand:
    """Tests for blocked pattern detection in raw (unparsed) commands."""

    def test_sudo_in_compound_detected(self):
        """Test that sudo inside a compound command is detected."""
        assert check_blocked_in_raw_command("for i in 1 2; do sudo echo $i; done", HARDCODED_BLOCKS) == "sudo"

    def test_su_in_compound_detected(self):
        """Test that su inside a compound command is detected."""
        assert check_blocked_in_raw_command("if true; then su - root; fi", HARDCODED_BLOCKS) == "su"

    def test_sudo_in_subshell_detected(self):
        """Test that sudo inside a subshell is detected."""
        assert check_blocked_in_raw_command("echo $(sudo whoami)", HARDCODED_BLOCKS) == "sudo"

    def test_normal_compound_not_blocked(self):
        """Test that normal compound commands are not blocked."""
        assert check_blocked_in_raw_command("for i in 1 2 3; do echo $i; done", HARDCODED_BLOCKS) is None

    def test_no_false_positives_from_substring(self):
        """Test that words containing 'su' as substring are NOT blocked."""
        assert check_blocked_in_raw_command("for f in issue result; do echo $f; done", HARDCODED_BLOCKS) is None
        assert check_blocked_in_raw_command("echo sum", HARDCODED_BLOCKS) is None

    def test_case_insensitive(self):
        """Test that blocking is case-insensitive."""
        assert check_blocked_in_raw_command("for i in 1; do SUDO echo $i; done", HARDCODED_BLOCKS) == "sudo"

    def test_deny_list_pattern_detected(self):
        """Test that deny list patterns are detected in raw commands."""
        deny_list = ["kubectl get secret", "rm"]
        assert check_blocked_in_raw_command("case $x in 1) kubectl get secret;; esac", deny_list) == "kubectl get secret"
        assert check_blocked_in_raw_command("case $x in 1) rm -rf /tmp;; esac", deny_list) == "rm"

    def test_deny_list_no_false_positives(self):
        """Test that deny list scanning doesn't have false positives from substrings."""
        deny_list = ["rm"]
        assert check_blocked_in_raw_command("case $x in 1) echo format;; esac", deny_list) is None


class TestGetEffectiveLists:
    """Tests for effective allow/deny list computation."""

    def test_none_config(self):
        """Test with builtin_allowlist='none'."""
        config = BashExecutorConfig(builtin_allowlist="none")
        allow_list, deny_list = get_effective_lists(config)
        assert allow_list == []
        assert deny_list == []

    def test_core_config_default(self):
        """Test that default config uses core allowlist."""
        config = BashExecutorConfig()
        allow_list, deny_list = get_effective_lists(config)
        # Core list includes kubectl and grep but not cat
        assert "kubectl get" in allow_list
        assert "kubectl describe" in allow_list
        assert "grep" in allow_list
        assert "cat" not in allow_list

    def test_extended_config(self):
        """Test with builtin_allowlist='extended'."""
        config = BashExecutorConfig(builtin_allowlist="extended")
        allow_list, deny_list = get_effective_lists(config)
        # Extended includes everything from core plus filesystem commands
        assert "kubectl get" in allow_list
        assert "grep" in allow_list
        assert "cat" in allow_list
        assert "find" in allow_list
        assert "ls" in allow_list

    def test_custom_lists(self):
        """Test with custom allow/deny lists."""
        config = BashExecutorConfig(
            builtin_allowlist="none",
            allow=["kubectl get", "grep"],
            deny=["kubectl delete"],
        )
        allow_list, deny_list = get_effective_lists(config)
        assert "kubectl get" in allow_list
        assert "grep" in allow_list
        assert "kubectl delete" in deny_list

    def test_extended_with_custom(self):
        """Test extended builtin list merged with custom entries."""
        config = BashExecutorConfig(
            builtin_allowlist="extended",
            allow=["custom-command"],
            deny=["custom-deny"],
        )
        allow_list, deny_list = get_effective_lists(config)

        # Should include builtins
        assert "kubectl get" in allow_list
        assert "grep" in allow_list
        assert "cat" in allow_list
        # Should include custom
        assert "custom-command" in allow_list
        # Should include custom deny
        assert "custom-deny" in deny_list

    def test_backwards_compat_include_default_true(self):
        """Test that deprecated include_default_allow_deny_list=True maps to extended."""
        config = BashExecutorConfig(include_default_allow_deny_list=True)
        assert config.builtin_allowlist == "extended"
        allow_list, deny_list = get_effective_lists(config)
        assert "kubectl get" in allow_list
        assert "cat" in allow_list

    def test_backwards_compat_include_default_false(self):
        """Test that deprecated include_default_allow_deny_list=False maps to none."""
        config = BashExecutorConfig(include_default_allow_deny_list=False)
        assert config.builtin_allowlist == "none"
        allow_list, deny_list = get_effective_lists(config)
        assert allow_list == []

    def test_default_lists_content(self):
        """Verify default lists have expected content."""
        # CORE_ALLOW_LIST has kubectl and text processing but not filesystem
        assert "kubectl get" in CORE_ALLOW_LIST
        assert "kubectl describe" in CORE_ALLOW_LIST
        assert "grep" in CORE_ALLOW_LIST
        assert "jq" in CORE_ALLOW_LIST
        assert "cat" not in CORE_ALLOW_LIST

        # EXTENDED_ALLOW_LIST has everything including filesystem
        assert "kubectl get" in EXTENDED_ALLOW_LIST
        assert "cat" in EXTENDED_ALLOW_LIST
        assert "find" in EXTENDED_ALLOW_LIST
        assert "ls" in EXTENDED_ALLOW_LIST

        # DEFAULT_DENY_LIST is empty by default - users configure their own
        assert len(DEFAULT_DENY_LIST) == 0


class TestValidateSegment:
    """Tests for single segment validation."""

    def test_allowed_command(self):
        """Test that allowed commands pass."""
        result = validate_segment(
            "kubectl get pods",
            allow_list=["kubectl get"],
            deny_list=[],
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_denied_command(self):
        """Test that denied commands are blocked."""
        result = validate_segment(
            "kubectl get secret my-secret",
            allow_list=["kubectl get"],
            deny_list=["kubectl get secret"],
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    def test_hardcoded_block(self):
        """Test that hardcoded blocks are always blocked."""
        result = validate_segment(
            "sudo kubectl get pods",
            allow_list=["sudo"],  # Even if in allow list
            deny_list=[],
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_approval_required(self):
        """Test that non-listed commands require approval."""
        result = validate_segment(
            "kubectl delete pod my-pod",
            allow_list=["kubectl get"],
            deny_list=[],
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED


class TestValidateCommand:
    """Tests for full command validation."""

    def test_simple_allowed_command(self):
        """Test a simple allowed command."""
        config = BashExecutorConfig(allow=["kubectl get"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods",
            ["kubectl get"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_piped_allowed_command(self):
        """Test a piped command where all segments are allowed."""
        config = BashExecutorConfig(allow=["kubectl get", "grep", "head"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods | grep error | head -10",
            ["kubectl get", "grep", "head"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_piped_command_partial_deny(self):
        """Test a piped command where one segment is denied."""
        config = BashExecutorConfig(
            allow=["kubectl get", "grep"],
            deny=["kubectl get secret"],
        )
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get secret | grep password",
            ["kubectl get secret", "grep"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    def test_subshell_all_allowed_is_allowed(self):
        """Subshell where all inner commands are in the allow list is allowed."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        # kubectl get is in CORE_ALLOW_LIST, echo is in allow - all segments allowed
        result = validate_command(
            "echo $(kubectl get secret)",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_subshell_inner_not_allowed_requires_approval(self):
        """Subshell with inner commands not in allow list requires approval."""
        config = BashExecutorConfig(allow=["echo"], builtin_allowlist="none")
        allow_list, deny_list = get_effective_lists(config)
        # Only echo is allowed, curl is not
        result = validate_command(
            "echo $(curl http://example.com)",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED

    def test_prefix_count_does_not_need_to_match_segment_count(self):
        """Test that prefix count doesn't need to match segment count."""
        config = BashExecutorConfig(allow=["kubectl get", "grep"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods | grep error",
            ["kubectl get"],  # Only 1 prefix for 2 segments - this is OK
            allow_list,
            deny_list,
        )
        # Command is allowed because all segments are in the allow list
        assert result.status == ValidationStatus.ALLOWED

    def test_hardcoded_block_in_pipe(self):
        """Test that hardcoded blocks are caught in piped commands."""
        config = BashExecutorConfig(allow=["sudo", "ls"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "sudo ls | grep file",
            ["sudo", "grep"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_approval_required_for_unknown(self):
        """Test that unknown commands require approval."""
        config = BashExecutorConfig(allow=["kubectl get"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl delete pod my-pod",
            ["kubectl delete"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.prefixes_needing_approval == ["kubectl delete"]

    def test_prefix_not_in_command_rejected(self):
        """Test that prefixes not appearing in the command are rejected."""
        config = BashExecutorConfig(allow=["kubectl get"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods",
            ["totally-fabricated-prefix"],  # Does not appear in command
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.PREFIX_NOT_IN_COMMAND

    def test_already_allowed_prefixes_filtered_from_approval(self):
        """Test that prefixes already in allow list are filtered from prefixes_needing_approval."""
        config = BashExecutorConfig(allow=["kubectl get"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods | custom-tool --flag",
            ["kubectl get", "custom-tool"],  # kubectl get is already allowed
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        # Only custom-tool should need approval, kubectl get is already allowed
        assert result.prefixes_needing_approval == ["custom-tool"]

    def test_duplicate_prefixes_deduplicated(self):
        """Test that duplicate prefixes in suggested_prefixes are deduplicated."""
        config = BashExecutorConfig(allow=[])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "custom-tool --flag | custom-tool --other",
            ["custom-tool", "custom-tool"],  # Same prefix twice
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        # Should only appear once in prefixes_needing_approval
        assert result.prefixes_needing_approval == ["custom-tool"]


class TestValidationOrder:
    """Tests to verify the validation order is correct."""

    def test_hardcoded_before_deny(self):
        """Test that hardcoded blocks are checked before deny list."""
        config = BashExecutorConfig(
            allow=[],
            deny=["sudo"],  # Sudo in deny list is redundant
        )
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command("sudo ls", ["sudo"], allow_list, deny_list)
        # Should be hardcoded block, not deny list
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_deny_before_allow(self):
        """Test that deny list is checked before allow list."""
        config = BashExecutorConfig(
            allow=["kubectl get"],  # General allow
            deny=["kubectl get secret"],  # More specific deny
        )
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get secret my-secret",
            ["kubectl get secret"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST


class TestHardcodedBlocksList:
    """Verify hardcoded blocks are as expected."""

    def test_hardcoded_blocks_content(self):
        """Verify the hardcoded blocks list."""
        assert "sudo" in HARDCODED_BLOCKS
        assert "su" in HARDCODED_BLOCKS


class TestUserConfiguredDenyList:
    """Tests for user-configured deny lists."""

    def test_default_deny_list_is_empty(self):
        """Verify DEFAULT_DENY_LIST is empty - users configure their own."""
        assert len(DEFAULT_DENY_LIST) == 0

    def test_user_configured_deny_blocks_command(self):
        """Test that user-configured deny list blocks commands."""
        config = BashExecutorConfig(
            builtin_allowlist="extended",
            deny=["kubectl get secret"],
        )
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get secrets -n default",
            ["kubectl get secrets"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    def test_user_configured_deny_path_syntax(self):
        """Test that user-configured deny blocks path syntax."""
        config = BashExecutorConfig(
            builtin_allowlist="extended",
            deny=["kubectl get secret"],
        )
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get secret/my-secret",
            ["kubectl get secret"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    def test_kubectl_get_pods_allowed_with_defaults(self):
        """Test that non-denied kubectl commands are allowed."""
        config = BashExecutorConfig(builtin_allowlist="extended")
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods -n default",
            ["kubectl get"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED


class TestCompoundStatements:
    """Tests for compound statement handling.

    Compound statements (for, while, if, case) require user approval.
    Inner command segments are still validated against deny/allow lists.
    """

    # ==================== SUPPORTED: Simple one-liner commands ====================

    def test_simple_command_allowed(self):
        """Simple single command is allowed."""
        config = BashExecutorConfig(allow=["kubectl get"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods -n default",
            ["kubectl get"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_pipe_command_allowed(self):
        """Pipe command (|) is allowed."""
        config = BashExecutorConfig(allow=["kubectl get", "grep"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods | grep Running",
            ["kubectl get", "grep"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_multiple_pipes_allowed(self):
        """Multiple pipes are allowed."""
        config = BashExecutorConfig(allow=["kubectl get", "grep", "head"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods | grep Running | head -5",
            ["kubectl get", "grep", "head"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_and_operator_allowed(self):
        """AND operator (&&) is allowed."""
        config = BashExecutorConfig(allow=["kubectl get"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods && kubectl get services",
            ["kubectl get", "kubectl get"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_or_operator_allowed(self):
        """OR operator (||) is allowed."""
        config = BashExecutorConfig(allow=["kubectl get", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods || echo 'no pods'",
            ["kubectl get", "echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_semicolon_operator_allowed(self):
        """Semicolon operator (;) is allowed."""
        config = BashExecutorConfig(allow=["kubectl get"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods; kubectl get services",
            ["kubectl get", "kubectl get"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_background_operator_allowed(self):
        """Background operator (&) is allowed."""
        config = BashExecutorConfig(allow=["sleep", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "sleep 5 & echo done",
            ["sleep", "echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_env_vars_in_command_allowed(self):
        """Environment variables in commands are allowed."""
        config = BashExecutorConfig(allow=["echo", "ls"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "echo $HOME && ls ${HOME}/projects",
            ["echo", "ls"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    # ==================== REQUIRES APPROVAL: Compound statements ====================

    def test_for_loop_requires_approval(self):
        """For loop requires user approval even when all prefixes are allowed."""
        config = BashExecutorConfig(allow=["for", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            'for i in 1 2 3 4 5; do echo "Iteration: $i"; done',
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Contains compound statements (for/while/if/etc)."
        assert result.prefixes_needing_approval == []

    def test_for_loop_with_command_requires_approval(self):
        """For loop iterating over command output requires user approval even when prefixes are allowed."""
        config = BashExecutorConfig(allow=["for", "kubectl"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "for pod in pod1 pod2; do kubectl logs $pod; done",
            ["kubectl logs"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Contains compound statements (for/while/if/etc)."
        assert result.prefixes_needing_approval == []

    def test_while_loop_requires_approval(self):
        """While loop requires user approval (sleep not in allow list)."""
        config = BashExecutorConfig(allow=["while", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "while true; do echo 'running'; sleep 1; done",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Contains compound statements (for/while/if/etc)."
        assert result.prefixes_needing_approval == []

    def test_until_loop_requires_approval(self):
        """Until loop requires user approval (false not in allow list)."""
        config = BashExecutorConfig(allow=["until", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "until false; do echo 'running'; done",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Contains compound statements (for/while/if/etc)."
        assert result.prefixes_needing_approval == []

    def test_if_statement_requires_approval(self):
        """If statement requires user approval ([ not in allow list)."""
        config = BashExecutorConfig(allow=["if", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "if [ -f /tmp/test ]; then echo 'exists'; fi",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Contains compound statements (for/while/if/etc)."
        assert result.prefixes_needing_approval == []

    def test_if_else_statement_requires_approval(self):
        """If-else statement requires user approval ([ not in allow list)."""
        config = BashExecutorConfig(allow=["if", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "if [ -f /tmp/test ]; then echo 'yes'; else echo 'no'; fi",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Contains compound statements (for/while/if/etc)."
        assert result.prefixes_needing_approval == []

    def test_case_statement_requires_approval(self):
        """Case statement requires user approval."""
        config = BashExecutorConfig(allow=["case", "echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "case $x in 1) echo one;; 2) echo two;; esac",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Command contains complex syntax which requires approval."
        assert result.prefixes_needing_approval == []

    # ==================== Subshells: validated via segment checking ====================

    def test_command_substitution_dollar_paren_all_allowed(self):
        """Command substitution $() with all inner commands allowed is allowed."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        # Both echo and whoami are in CORE_ALLOW_LIST
        result = validate_command(
            "echo $(whoami)",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_command_substitution_dollar_paren_inner_not_allowed(self):
        """Command substitution $() with inner command not allowed requires approval."""
        config = BashExecutorConfig(allow=["echo"], builtin_allowlist="none")
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "echo $(whoami)",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert "Segment(s) not in allow list: 'whoami'" in result.message

    def test_command_substitution_backticks_all_allowed(self):
        """Command substitution with backticks with all commands allowed is allowed."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        # Both echo and whoami are in CORE_ALLOW_LIST
        result = validate_command(
            "echo `whoami`",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_command_substitution_backticks_inner_not_allowed(self):
        """Command substitution with backticks with inner not allowed requires approval."""
        config = BashExecutorConfig(allow=["echo"], builtin_allowlist="none")
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "echo `whoami`",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert "Segment(s) not in allow list: 'whoami'" in result.message

    def test_process_substitution_all_allowed(self):
        """Process substitution with all inner commands allowed is allowed."""
        config = BashExecutorConfig(allow=["diff", "cat"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "diff <(cat file1) <(cat file2)",
            ["diff"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_process_substitution_inner_not_allowed(self):
        """Process substitution with inner commands not in allow list requires approval."""
        config = BashExecutorConfig(allow=["diff"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "diff <(cat file1) <(cat file2)",
            ["diff"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert "Segment(s) not in allow list: 'cat file1', 'cat file2'" in result.message

    # ==================== STILL BLOCKED: Hardcoded blocks inside scripts ====================

    def test_subshell_with_sudo_still_denied(self):
        """Subshell containing sudo is still blocked."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "echo $(sudo whoami)",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_compound_with_sudo_still_denied(self):
        """Compound statement containing sudo is still blocked."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "for i in 1 2 3; do sudo echo $i; done",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_compound_with_su_still_denied(self):
        """Compound statement containing su is still blocked."""
        config = BashExecutorConfig(allow=["su"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "if true; then su - root; fi",
            ["su"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    # ==================== STILL BLOCKED: Deny-listed commands inside compound statements ====================

    def test_compound_with_deny_listed_command_still_denied(self):
        """Compound statement with a deny-listed command should be denied."""
        config = BashExecutorConfig(allow=["kubectl get"], deny=["kubectl get secret"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "for ns in ns1 ns2; do kubectl get secret -n $ns; done",
            ["kubectl get secret"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    def test_subshell_with_deny_listed_command_still_denied(self):
        """Subshell with a deny-listed inner command should be denied."""
        config = BashExecutorConfig(allow=["echo"], deny=["rm"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "echo $(rm -rf /tmp)",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    # ==================== Unparseable commands: raw string safety checks ====================

    def test_case_with_deny_listed_command_denied(self):
        """Case statement (unparseable) with deny-listed command is denied via raw string scan."""
        config = BashExecutorConfig(allow=["echo"], deny=["kubectl get secret"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "case $x in 1) kubectl get secret;; 2) echo two;; esac",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    def test_case_with_sudo_denied(self):
        """Case statement (unparseable) with sudo is denied via raw string scan."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "case $x in 1) sudo echo one;; esac",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_unparseable_command_requires_approval(self):
        """Unparseable command with no blocked patterns requires approval."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "case $x in 1) echo one;; 2) echo two;; esac",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.APPROVAL_REQUIRED
        assert result.message == "Command contains complex syntax which requires approval."


# ==================== Integration: requires_approval prefixes_to_save ====================


class TestRequiresApprovalPrefixesToSave:
    """Test that requires_approval passes correct prefixes_to_save to ApprovalRequirement.

    Only unapproved-segment approvals should save prefixes. Compound and unparseable
    command approvals are one-time only — they must not save prefixes to the allow list.
    """

    @pytest.fixture
    def tool(self):
        toolset = BashExecutorToolset()
        toolset.config = BashExecutorConfig(builtin_allowlist="none", allow=["echo"])
        return toolset.tools[0]

    @pytest.fixture
    def context(self):
        ctx = MagicMock()
        ctx.session_approved_prefixes = []
        return ctx

    def test_compound_command_saves_no_prefixes(self, tool, context):
        """Compound commands (for/while/if) should not save any prefixes on approval."""
        params = {
            "command": 'for i in 1 2 3; do echo "hello"; done',
            "suggested_prefixes": ["echo"],
        }
        result = tool.requires_approval(params, context)
        assert result is not None
        assert result.needs_approval is True
        assert result.prefixes_to_save == []

    def test_unparseable_command_saves_no_prefixes(self, tool, context):
        """Unparseable commands (case statements) should not save any prefixes on approval."""
        params = {
            "command": "case $x in 1) echo one;; 2) echo two;; esac",
            "suggested_prefixes": ["echo"],
        }
        result = tool.requires_approval(params, context)
        assert result is not None
        assert result.needs_approval is True
        assert result.prefixes_to_save == []

    def test_unapproved_segment_saves_prefixes(self, tool, context):
        """Unapproved segments should save their prefixes for future auto-approval."""
        params = {
            "command": "mycustomtool --flag",
            "suggested_prefixes": ["mycustomtool"],
        }
        result = tool.requires_approval(params, context)
        assert result is not None
        assert result.needs_approval is True
        assert result.prefixes_to_save == ["mycustomtool"]

    def test_piped_command_with_unapproved_segment_saves_only_unapproved(self, tool, context):
        """Piped command where one segment is unapproved saves only the unapproved prefix."""
        params = {
            "command": "echo hello | mycustomtool --process",
            "suggested_prefixes": ["echo", "mycustomtool"],
        }
        result = tool.requires_approval(params, context)
        assert result is not None
        assert result.needs_approval is True
        assert result.prefixes_to_save == ["mycustomtool"]

    def test_allowed_command_no_approval_needed(self, tool, context):
        """Fully allowed commands should not require approval."""
        params = {
            "command": "echo hello",
            "suggested_prefixes": ["echo"],
        }
        result = tool.requires_approval(params, context)
        assert result is None
