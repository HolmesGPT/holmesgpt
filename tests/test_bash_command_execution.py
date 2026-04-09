"""
Unit tests for the bash command execution module.

Tests execute_bash_command function including timeout handling and output capture.
"""

from holmes.plugins.toolsets.bash.common.bash import BashResult, execute_bash_command


class TestExecuteBashCommand:
    """Tests for execute_bash_command function - basic execution."""

    def test_successful_command(self):
        """Test successful command execution."""
        result = execute_bash_command("echo hello", timeout=5)
        assert isinstance(result, BashResult)
        assert result.return_code == 0
        assert result.timed_out is False
        assert "hello" in result.stdout

    def test_command_with_multiple_lines(self):
        """Test command that produces multiple lines of output."""
        result = execute_bash_command("echo -e 'line1\nline2\nline3'", timeout=5)
        assert result.return_code == 0
        assert result.timed_out is False
        assert "line1" in result.stdout
        assert "line2" in result.stdout
        assert "line3" in result.stdout

    def test_command_with_no_output(self):
        """Test command that produces no output."""
        result = execute_bash_command("true", timeout=5)
        assert result.return_code == 0
        assert result.timed_out is False

    def test_command_failure_exit_code_1(self):
        """Test command that fails with exit code 1."""
        result = execute_bash_command("exit 1", timeout=5)
        assert result.return_code == 1
        assert result.timed_out is False

    def test_command_failure_exit_code_42(self):
        """Test command that fails with custom exit code."""
        result = execute_bash_command("exit 42", timeout=5)
        assert result.return_code == 42
        assert result.timed_out is False

    def test_command_with_stderr(self):
        """Test that stderr is captured (merged with stdout)."""
        result = execute_bash_command("echo 'error message' >&2", timeout=5)
        # stderr is merged with stdout
        assert "error message" in result.stdout
        assert result.return_code == 0

    def test_command_with_both_stdout_and_stderr(self):
        """Test command with both stdout and stderr output."""
        result = execute_bash_command("echo stdout; echo stderr >&2", timeout=5)
        assert "stdout" in result.stdout
        assert "stderr" in result.stdout
        assert result.return_code == 0

    def test_command_with_special_characters(self):
        """Test command with special characters in output."""
        result = execute_bash_command("echo 'hello$world'", timeout=5)
        assert "hello" in result.stdout
        assert result.return_code == 0

    def test_command_with_quotes(self):
        """Test command with quoted strings."""
        result = execute_bash_command('echo "hello world"', timeout=5)
        assert "hello world" in result.stdout
        assert result.return_code == 0

    def test_command_with_pipe(self):
        """Test command with pipe operator."""
        result = execute_bash_command("echo 'hello world' | tr 'a-z' 'A-Z'", timeout=5)
        assert "HELLO WORLD" in result.stdout
        assert result.return_code == 0

    def test_command_with_environment_variable(self):
        """Test command accessing environment variable."""
        result = execute_bash_command("echo $HOME", timeout=5)
        assert result.return_code == 0
        assert result.stdout != ""  # Should have some home directory

    def test_command_not_found(self):
        """Test handling of command not found (via bash)."""
        result = execute_bash_command("nonexistent_command_xyz123", timeout=5)
        # Bash handles this with non-zero exit code
        assert result.return_code != 0
        assert result.timed_out is False
        assert "not found" in result.stdout.lower()


class TestExecuteBashCommandTimeout:
    """Tests for timeout handling in execute_bash_command."""

    def test_timeout_returns_timed_out_flag(self):
        """Test that timeout sets the timed_out flag."""
        # Use short sleep (2s) with very short timeout (0.5s)
        result = execute_bash_command("sleep 2", timeout=1)
        assert result.timed_out is True
        assert result.return_code is None

    def test_timeout_captures_partial_output(self):
        """Test that partial output is captured on timeout."""
        # Command that outputs before sleeping
        cmd = "echo 'output before sleep'; sleep 2"
        result = execute_bash_command(cmd, timeout=1)
        assert result.timed_out is True
        assert "output before sleep" in result.stdout

    def test_timeout_captures_multiple_lines_partial_output(self):
        """Test that multiple lines of partial output are captured on timeout."""
        cmd = "echo 'line1'; echo 'line2'; echo 'line3'; sleep 2"
        result = execute_bash_command(cmd, timeout=1)
        assert result.timed_out is True
        assert "line1" in result.stdout
        assert "line2" in result.stdout
        assert "line3" in result.stdout

    def test_timeout_with_short_timeout_value(self):
        """Test timeout with very short timeout value."""
        result = execute_bash_command("sleep 2", timeout=1)
        assert result.timed_out is True
        assert result.return_code is None

    def test_no_timeout_when_command_finishes_quickly(self):
        """Test that fast commands don't timeout."""
        result = execute_bash_command("echo fast", timeout=5)
        assert result.timed_out is False
        assert result.return_code == 0
        assert "fast" in result.stdout


class TestBashResultDataclass:
    """Tests for the BashResult dataclass."""

    def test_bash_result_fields(self):
        """Test that BashResult has expected fields."""
        result = BashResult(
            stdout="test output",
            return_code=0,
            timed_out=False,
        )
        assert result.stdout == "test output"
        assert result.return_code == 0
        assert result.timed_out is False

    def test_bash_result_timeout(self):
        """Test BashResult representing a timeout."""
        result = BashResult(
            stdout="partial output",
            return_code=None,
            timed_out=True,
        )
        assert result.timed_out is True
        assert result.return_code is None
        assert result.stdout == "partial output"
