"""
Unit tests for the bash toolset validation module.

Tests prefix-based command validation, subshell detection, and allow/deny list handling.
"""

from holmes.plugins.toolsets.bash.common.config import (
    HARDCODED_BLOCKS,
    BashExecutorConfig,
)
from holmes.plugins.toolsets.bash.common.default_lists import (
    DEFAULT_ALLOW_LIST,
    DEFAULT_DENY_LIST,
)
from holmes.plugins.toolsets.bash.validation import (
    DenyReason,
    ValidationStatus,
    check_hardcoded_blocks,
    detect_subshells,
    get_effective_lists,
    match_prefix,
    match_prefix_for_deny,
    parse_command_segments,
    validate_command,
    validate_prefix_for_segment,
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
        segments = parse_command_segments("kubectl get pods")
        assert segments == ["kubectl get pods"]

    def test_piped_command(self):
        """Test parsing a piped command."""
        segments = parse_command_segments("kubectl get pods | grep error")
        assert segments == ["kubectl get pods", "grep error"]

    def test_multiple_pipes(self):
        """Test parsing multiple pipes."""
        segments = parse_command_segments("kubectl get pods | grep error | head -10")
        assert segments == ["kubectl get pods", "grep error", "head -10"]

    def test_and_operator(self):
        """Test parsing && operator."""
        segments = parse_command_segments("mkdir test && cd test")
        assert segments == ["mkdir test", "cd test"]

    def test_or_operator(self):
        """Test parsing || operator."""
        segments = parse_command_segments("test -f file.txt || touch file.txt")
        assert segments == ["test -f file.txt", "touch file.txt"]

    def test_semicolon_operator(self):
        """Test parsing ; operator."""
        segments = parse_command_segments("echo hello; echo world")
        assert segments == ["echo hello", "echo world"]

    def test_background_operator(self):
        """Test parsing & operator."""
        segments = parse_command_segments("sleep 10 & echo done")
        assert segments == ["sleep 10", "echo done"]

    def test_empty_segments_filtered(self):
        """Test that empty segments are filtered out."""
        segments = parse_command_segments("  |  kubectl get pods  |  ")
        assert "kubectl get pods" in segments
        assert "" not in segments


class TestDetectSubshells:
    """Tests for subshell detection."""

    def test_no_subshell(self):
        """Test that commands without subshells pass."""
        assert not detect_subshells("kubectl get pods")
        assert not detect_subshells("echo hello world")
        assert not detect_subshells("ls -la /var/log")

    def test_dollar_paren_subshell(self):
        """Test detection of $() subshells."""
        assert detect_subshells("echo $(whoami)")
        assert detect_subshells("kubectl get pods -n $(kubectl config current-context)")

    def test_backtick_subshell(self):
        """Test detection of backtick subshells."""
        assert detect_subshells("echo `whoami`")
        assert detect_subshells("kubectl get pods -n `kubectl config current-context`")

    def test_process_substitution_input(self):
        """Test detection of <() process substitution."""
        assert detect_subshells("diff <(cat file1) <(cat file2)")

    def test_process_substitution_output(self):
        """Test detection of >() process substitution."""
        assert detect_subshells("tee >(cat > file)")

    def test_env_vars_allowed(self):
        """Test that environment variables are allowed."""
        assert not detect_subshells("echo $HOME")
        assert not detect_subshells("ls ${HOME}/projects")
        assert not detect_subshells("echo $USER at $HOSTNAME")


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


class TestGetEffectiveLists:
    """Tests for effective allow/deny list computation."""

    def test_empty_config(self):
        """Test with empty config."""
        config = BashExecutorConfig()
        allow_list, deny_list = get_effective_lists(config)
        assert allow_list == []
        assert deny_list == []

    def test_custom_lists(self):
        """Test with custom allow/deny lists."""
        config = BashExecutorConfig(
            allow=["kubectl get", "grep"],
            deny=["kubectl delete"],
        )
        allow_list, deny_list = get_effective_lists(config)
        assert "kubectl get" in allow_list
        assert "grep" in allow_list
        assert "kubectl delete" in deny_list

    def test_include_defaults(self):
        """Test with default lists included."""
        config = BashExecutorConfig(
            include_default_allow_deny_list=True,
            allow=["custom-command"],
            deny=["custom-deny"],
        )
        allow_list, deny_list = get_effective_lists(config)

        # Should include defaults
        assert "kubectl get" in allow_list
        assert "grep" in allow_list
        # Should include custom
        assert "custom-command" in allow_list
        # Should include custom deny
        assert "custom-deny" in deny_list

    def test_default_lists_content(self):
        """Verify default lists have expected content."""
        # Check DEFAULT_ALLOW_LIST has key commands
        assert "kubectl get" in DEFAULT_ALLOW_LIST
        assert "kubectl describe" in DEFAULT_ALLOW_LIST
        assert "grep" in DEFAULT_ALLOW_LIST
        assert "cat" in DEFAULT_ALLOW_LIST
        assert "kube-lineage" in DEFAULT_ALLOW_LIST
        assert "jq" in DEFAULT_ALLOW_LIST

        # DEFAULT_DENY_LIST is empty by default - users configure their own
        assert len(DEFAULT_DENY_LIST) == 0


class TestValidatePrefixForSegment:
    """Tests for single segment validation."""

    def test_allowed_command(self):
        """Test that allowed commands pass."""
        result = validate_prefix_for_segment(
            "kubectl get pods",
            "kubectl get",
            allow_list=["kubectl get"],
            deny_list=[],
        )
        assert result.status == ValidationStatus.ALLOWED

    def test_denied_command(self):
        """Test that denied commands are blocked."""
        result = validate_prefix_for_segment(
            "kubectl get secret my-secret",
            "kubectl get secret",
            allow_list=["kubectl get"],
            deny_list=["kubectl get secret"],
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.DENY_LIST

    def test_hardcoded_block(self):
        """Test that hardcoded blocks are always blocked."""
        result = validate_prefix_for_segment(
            "sudo kubectl get pods",
            "sudo",
            allow_list=["sudo"],  # Even if in allow list
            deny_list=[],
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.HARDCODED_BLOCK

    def test_prefix_mismatch(self):
        """Test that mismatched prefixes are rejected."""
        result = validate_prefix_for_segment(
            "kubectl delete pod my-pod",
            "kubectl get",  # Wrong prefix
            allow_list=["kubectl get"],
            deny_list=[],
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.PREFIX_MISMATCH

    def test_approval_required(self):
        """Test that non-listed commands require approval."""
        result = validate_prefix_for_segment(
            "kubectl delete pod my-pod",
            "kubectl delete",
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

    def test_subshell_detection(self):
        """Test that subshells are blocked."""
        config = BashExecutorConfig(allow=["echo"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "echo $(kubectl get secret)",
            ["echo"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.SUBSHELL_DETECTED

    def test_prefix_count_mismatch(self):
        """Test that prefix count must match segment count."""
        config = BashExecutorConfig(allow=["kubectl get", "grep"])
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods | grep error",
            ["kubectl get"],  # Only 1 prefix for 2 segments
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.DENIED
        assert result.deny_reason == DenyReason.PREFIX_COUNT_MISMATCH

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
            include_default_allow_deny_list=True,
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
            include_default_allow_deny_list=True,
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
        config = BashExecutorConfig(include_default_allow_deny_list=True)
        allow_list, deny_list = get_effective_lists(config)
        result = validate_command(
            "kubectl get pods -n default",
            ["kubectl get"],
            allow_list,
            deny_list,
        )
        assert result.status == ValidationStatus.ALLOWED
