"""
Unit tests for the bash toolset module.

Tests bash_result_to_structured conversion function.
"""

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.bash.bash_toolset import bash_result_to_structured
from holmes.plugins.toolsets.bash.common.bash import BashResult


class TestBashResultToStructured:
    """Tests for bash_result_to_structured conversion function."""

    def test_success_with_output(self):
        """Test conversion of successful result with output."""
        bash_result = BashResult(stdout="hello", return_code=0, timed_out=False)
        structured = bash_result_to_structured(
            bash_result, cmd="echo hello", timeout=5, params={"key": "value"}
        )

        assert structured.status == StructuredToolResultStatus.SUCCESS
        assert structured.error is None
        assert "hello" in structured.data
        assert structured.return_code == 0
        assert structured.params == {"key": "value"}
        assert structured.invocation == "echo hello"

    def test_success_without_output(self):
        """Test conversion of successful result without output (NO_DATA)."""
        bash_result = BashResult(stdout="", return_code=0, timed_out=False)
        structured = bash_result_to_structured(
            bash_result, cmd="true", timeout=5, params={}
        )

        assert structured.status == StructuredToolResultStatus.NO_DATA
        assert structured.error is None
        assert structured.return_code == 0

    def test_failure_with_non_zero_exit(self):
        """Test conversion of failed result with non-zero exit code."""
        bash_result = BashResult(stdout="error output", return_code=1, timed_out=False)
        structured = bash_result_to_structured(
            bash_result, cmd="exit 1", timeout=5, params={}
        )

        assert structured.status == StructuredToolResultStatus.ERROR
        assert "non-zero exit status 1" in structured.error
        assert structured.return_code == 1

    def test_timeout_with_partial_output(self):
        """Test conversion of timed out result with partial output."""
        bash_result = BashResult(
            stdout="partial output", return_code=None, timed_out=True
        )
        structured = bash_result_to_structured(
            bash_result, cmd="sleep 10", timeout=1, params={}
        )

        assert structured.status == StructuredToolResultStatus.ERROR
        assert "timed out after 1 seconds" in structured.error
        # Partial output is in data, not duplicated in error
        assert structured.data is not None
        assert "partial output" in structured.data

    def test_timeout_without_output(self):
        """Test conversion of timed out result without output."""
        bash_result = BashResult(stdout="", return_code=None, timed_out=True)
        structured = bash_result_to_structured(
            bash_result, cmd="sleep 10", timeout=1, params={}
        )

        assert structured.status == StructuredToolResultStatus.ERROR
        assert "timed out after 1 seconds" in structured.error
        assert structured.data is None
