"""Tests for the coding agent delegation toolset."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.tools import (
    StructuredToolResultStatus,
    ToolInvokeContext,
)
from holmes.plugins.toolsets.coding_agent.coding_agent_toolset import (
    CodingAgentConfig,
    CodingAgentToolset,
    DelegateToAgentTool,
    _clone_repo,
    _commit_and_push,
    _create_branch,
    _has_changes,
    _run_coding_agent,
)
from holmes.plugins.toolsets.coding_agent.git_provider import (
    GitHubProvider,
    GitLabProvider,
    create_git_provider,
)


# ---------------------------------------------------------------------------
# Git provider tests
# ---------------------------------------------------------------------------


class TestGitHubProvider:
    def test_authenticated_clone_url(self):
        provider = GitHubProvider(token="ghp_testtoken123")
        url = provider.get_authenticated_clone_url("https://github.com/org/repo")
        assert url == "https://x-access-token:ghp_testtoken123@github.com/org/repo"

    def test_parse_owner_repo(self):
        provider = GitHubProvider(token="tok")
        owner, repo = provider._parse_owner_repo("https://github.com/my-org/my-repo")
        assert owner == "my-org"
        assert repo == "my-repo"

    def test_parse_owner_repo_with_git_suffix(self):
        provider = GitHubProvider(token="tok")
        owner, repo = provider._parse_owner_repo(
            "https://github.com/my-org/my-repo.git"
        )
        assert owner == "my-org"
        assert repo == "my-repo"

    def test_parse_owner_repo_with_trailing_slash(self):
        provider = GitHubProvider(token="tok")
        owner, repo = provider._parse_owner_repo("https://github.com/my-org/my-repo/")
        assert owner == "my-org"
        assert repo == "my-repo"

    def test_parse_owner_repo_invalid(self):
        provider = GitHubProvider(token="tok")
        with pytest.raises(ValueError, match="Cannot parse GitHub"):
            provider._parse_owner_repo("not-a-url")

    @patch("holmes.plugins.toolsets.coding_agent.git_provider.requests.post")
    def test_create_pull_request_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/org/repo/pull/42"
        }
        mock_post.return_value = mock_response

        provider = GitHubProvider(token="ghp_tok")
        pr_url = provider.create_pull_request(
            repo_url="https://github.com/org/repo",
            head_branch="holmes/fix-abc123",
            base_branch="main",
            title="Fix issue",
            body="Description",
            draft=True,
        )
        assert pr_url == "https://github.com/org/repo/pull/42"

        # Verify the API call
        call_args = mock_post.call_args
        assert "/repos/org/repo/pulls" in call_args.args[0]
        assert call_args.kwargs["json"]["draft"] is True

    @patch("holmes.plugins.toolsets.coding_agent.git_provider.requests.post")
    def test_create_pull_request_failure(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Validation Failed"
        mock_post.return_value = mock_response

        provider = GitHubProvider(token="ghp_tok")
        with pytest.raises(RuntimeError, match="Failed to create GitHub PR"):
            provider.create_pull_request(
                repo_url="https://github.com/org/repo",
                head_branch="branch",
                base_branch="main",
                title="title",
                body="body",
            )


class TestGitLabProvider:
    def test_authenticated_clone_url(self):
        provider = GitLabProvider(token="glpat-testtoken123")
        url = provider.get_authenticated_clone_url("https://gitlab.com/org/repo")
        assert url == "https://oauth2:glpat-testtoken123@gitlab.com/org/repo"

    def test_authenticated_clone_url_self_hosted(self):
        provider = GitLabProvider(token="glpat-tok")
        url = provider.get_authenticated_clone_url(
            "https://gitlab.mycompany.com/org/repo"
        )
        assert url == "https://oauth2:glpat-tok@gitlab.mycompany.com/org/repo"

    def test_parse_project_path(self):
        provider = GitLabProvider(token="tok")
        path = provider._parse_project_path("https://gitlab.com/my-org/my-repo")
        assert path == "my-org%2Fmy-repo"

    def test_parse_project_path_subgroups(self):
        provider = GitLabProvider(token="tok")
        path = provider._parse_project_path("https://gitlab.com/org/sub/repo")
        assert path == "org%2Fsub%2Frepo"

    def test_parse_project_path_invalid(self):
        provider = GitLabProvider(token="tok")
        with pytest.raises(ValueError, match="Cannot parse GitLab"):
            provider._parse_project_path("not-a-url")

    @patch("holmes.plugins.toolsets.coding_agent.git_provider.requests.post")
    def test_create_merge_request_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "web_url": "https://gitlab.com/org/repo/-/merge_requests/7"
        }
        mock_post.return_value = mock_response

        provider = GitLabProvider(token="glpat-tok")
        mr_url = provider.create_pull_request(
            repo_url="https://gitlab.com/org/repo",
            head_branch="holmes/fix-abc",
            base_branch="main",
            title="Fix issue",
            body="Description",
            draft=True,
        )
        assert mr_url == "https://gitlab.com/org/repo/-/merge_requests/7"

        # Verify draft prefix is added to title
        call_args = mock_post.call_args
        assert call_args.kwargs["json"]["title"] == "Draft: Fix issue"

    @patch("holmes.plugins.toolsets.coding_agent.git_provider.requests.post")
    def test_create_merge_request_non_draft(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "web_url": "https://gitlab.com/org/repo/-/merge_requests/7"
        }
        mock_post.return_value = mock_response

        provider = GitLabProvider(token="glpat-tok")
        provider.create_pull_request(
            repo_url="https://gitlab.com/org/repo",
            head_branch="branch",
            base_branch="main",
            title="Fix issue",
            body="body",
            draft=False,
        )
        call_args = mock_post.call_args
        assert call_args.kwargs["json"]["title"] == "Fix issue"


class TestCreateGitProvider:
    def test_create_github(self):
        provider = create_git_provider("github", "tok")
        assert isinstance(provider, GitHubProvider)

    def test_create_gitlab(self):
        provider = create_git_provider("gitlab", "tok")
        assert isinstance(provider, GitLabProvider)

    def test_create_invalid(self):
        with pytest.raises(ValueError, match="Unsupported git provider"):
            create_git_provider("bitbucket", "tok")


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestCodingAgentConfig:
    def test_defaults(self):
        config = CodingAgentConfig(git_token="tok")
        assert config.git_provider_type == "github"
        assert config.max_turns == 30
        assert config.draft_pr is True
        assert config.timeout_seconds == 600
        assert config.model is None
        assert config.allowed_tools is None
        assert config.disallowed_tools is None

    def test_custom_values(self):
        config = CodingAgentConfig(
            git_token="tok",
            git_provider_type="gitlab",
            max_turns=10,
            model="claude-sonnet-4-5-20250929",
            draft_pr=False,
        )
        assert config.git_provider_type == "gitlab"
        assert config.max_turns == 10
        assert config.model == "claude-sonnet-4-5-20250929"
        assert config.draft_pr is False


# ---------------------------------------------------------------------------
# Git helper function tests
# ---------------------------------------------------------------------------


class TestCloneRepo:
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.tempfile.mkdtemp")
    def test_clone_success(self, mock_mkdtemp, mock_run):
        mock_mkdtemp.return_value = "/tmp/holmes-coding-agent-abc"
        mock_run.return_value = MagicMock(returncode=0)

        provider = GitHubProvider(token="tok")
        workdir = _clone_repo("https://github.com/org/repo", "main", provider)

        assert workdir == "/tmp/holmes-coding-agent-abc"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "git" in call_args.args[0]
        assert "clone" in call_args.args[0]
        assert "--depth=1" in call_args.args[0]

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.shutil.rmtree")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.tempfile.mkdtemp")
    def test_clone_failure(self, mock_mkdtemp, mock_run, mock_rmtree):
        mock_mkdtemp.return_value = "/tmp/holmes-coding-agent-abc"
        mock_run.side_effect = subprocess.CalledProcessError(
            128, "git clone", stderr="fatal: repository not found"
        )

        provider = GitHubProvider(token="tok")
        with pytest.raises(RuntimeError, match="Failed to clone"):
            _clone_repo("https://github.com/org/repo", "main", provider)

        # Verify cleanup happened
        mock_rmtree.assert_called_once_with(
            "/tmp/holmes-coding-agent-abc", ignore_errors=True
        )

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.shutil.rmtree")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.tempfile.mkdtemp")
    def test_clone_timeout(self, mock_mkdtemp, mock_run, mock_rmtree):
        mock_mkdtemp.return_value = "/tmp/holmes-coding-agent-abc"
        mock_run.side_effect = subprocess.TimeoutExpired("git clone", 120)

        provider = GitHubProvider(token="tok")
        with pytest.raises(RuntimeError, match="timed out"):
            _clone_repo("https://github.com/org/repo", "main", provider)

        mock_rmtree.assert_called_once()


class TestCreateBranch:
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_create_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        _create_branch("/tmp/repo", "holmes/fix-123")
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args.args[0] == ["git", "checkout", "-b", "holmes/fix-123"]
        assert call_args.kwargs["cwd"] == "/tmp/repo"


class TestHasChanges:
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_has_changes(self, mock_run):
        mock_run.return_value = MagicMock(stdout="M file.py\n")
        assert _has_changes("/tmp/repo") is True

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_no_changes(self, mock_run):
        mock_run.return_value = MagicMock(stdout="")
        assert _has_changes("/tmp/repo") is False


class TestCommitAndPush:
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_commit_and_push(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitHubProvider(token="tok")

        _commit_and_push(
            workdir="/tmp/repo",
            branch_name="holmes/fix-123",
            commit_message="fix: something",
            git_provider=provider,
            repo_url="https://github.com/org/repo",
        )

        # Should call: git add, git commit, git remote set-url, git push
        assert mock_run.call_count == 4
        commands = [call.args[0] for call in mock_run.call_args_list]
        assert commands[0] == ["git", "add", "-A"]
        assert commands[1][:3] == ["git", "commit", "-m"]
        assert commands[2][:3] == ["git", "remote", "set-url"]
        assert commands[3][:3] == ["git", "push", "-u"]


# ---------------------------------------------------------------------------
# Run coding agent tests
# ---------------------------------------------------------------------------


class TestRunCodingAgent:
    def test_run_agent_success(self):
        """Test that the agent SDK is called correctly and results are parsed."""
        import asyncio

        from claude_agent_sdk import ResultMessage

        config = CodingAgentConfig(git_token="tok", max_turns=5)

        async def mock_query(**kwargs):
            yield ResultMessage(
                subtype="result",
                duration_ms=5000,
                duration_api_ms=4000,
                is_error=False,
                num_turns=3,
                session_id="test-session",
                total_cost_usd=0.05,
                result="Fixed the bug in auth.py",
            )

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            result_text, num_turns, cost = asyncio.run(
                _run_coding_agent("/tmp/repo", "Fix auth bug", config)
            )

        assert "Fixed the bug in auth.py" in result_text
        assert num_turns == 3
        assert cost == 0.05

    def test_run_agent_error(self):
        """Test that agent errors are raised properly."""
        import asyncio

        from claude_agent_sdk import ResultMessage

        config = CodingAgentConfig(git_token="tok")

        async def mock_query(**kwargs):
            yield ResultMessage(
                subtype="result",
                duration_ms=1000,
                duration_api_ms=800,
                is_error=True,
                num_turns=1,
                session_id="test-session",
                result="Error: could not find file",
            )

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            with pytest.raises(RuntimeError, match="Coding agent failed"):
                asyncio.run(_run_coding_agent("/tmp/repo", "Fix something", config))

    def test_run_agent_cli_not_found(self):
        """Test handling when Claude CLI is not installed."""
        import asyncio

        from claude_agent_sdk import CLINotFoundError

        config = CodingAgentConfig(git_token="tok")

        async def mock_query(**kwargs):
            raise CLINotFoundError("CLI not found")
            yield  # Make it a generator  # noqa: E501

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            with pytest.raises(RuntimeError, match="Claude Code CLI not found"):
                asyncio.run(_run_coding_agent("/tmp/repo", "Fix something", config))


# ---------------------------------------------------------------------------
# DelegateToAgentTool invocation tests
# ---------------------------------------------------------------------------


class TestDelegateToAgentTool:
    @pytest.fixture
    def toolset(self):
        toolset = CodingAgentToolset()
        toolset.config = CodingAgentConfig(git_token="ghp_tok123")
        toolset._git_provider = GitHubProvider(token="ghp_tok123")
        return toolset

    @pytest.fixture
    def tool(self, toolset):
        return DelegateToAgentTool(toolset)

    @pytest.fixture
    def context(self):
        return ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=True,
            llm=None,
            max_token_count=4000,
            tool_call_id="test-id",
            tool_name="delegate_to_coding_agent",
            request_context=None,
        )

    def test_missing_task(self, tool, context):
        result = tool._invoke({"repo_url": "https://github.com/org/repo"}, context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "task" in result.error.lower()

    def test_missing_repo_url(self, tool, context):
        result = tool._invoke({"task": "Fix something"}, context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "repo_url" in result.error.lower()

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.shutil.rmtree")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._clone_repo")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._create_branch")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.asyncio.run")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._has_changes")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._commit_and_push")
    def test_full_flow_no_changes(
        self,
        mock_push,
        mock_changes,
        mock_async_run,
        mock_branch,
        mock_clone,
        mock_rmtree,
        tool,
        context,
    ):
        """Test the case where agent makes no changes."""
        mock_clone.return_value = "/tmp/repo"
        mock_async_run.return_value = ("No changes needed", 2, 0.01)
        mock_changes.return_value = False

        result = tool._invoke(
            {
                "task": "Fix the bug",
                "repo_url": "https://github.com/org/repo",
                "base_branch": "main",
                "pr_title": "Fix bug",
            },
            context,
        )
        assert result.status == StructuredToolResultStatus.NO_DATA
        assert "did not make any changes" in result.data
        mock_push.assert_not_called()

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.shutil.rmtree")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._clone_repo")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._create_branch")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.asyncio.run")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._has_changes")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._commit_and_push")
    def test_full_flow_success(
        self,
        mock_push,
        mock_changes,
        mock_async_run,
        mock_branch,
        mock_clone,
        mock_rmtree,
        tool,
        toolset,
        context,
    ):
        """Test a complete successful delegation."""
        mock_clone.return_value = "/tmp/repo"
        mock_async_run.return_value = ("Fixed the auth bug", 5, 0.08)
        mock_changes.return_value = True

        # Mock the PR creation
        with patch.object(
            toolset._git_provider,
            "create_pull_request",
            return_value="https://github.com/org/repo/pull/99",
        ):
            result = tool._invoke(
                {
                    "task": "Fix auth bug",
                    "repo_url": "https://github.com/org/repo",
                    "base_branch": "main",
                    "pr_title": "Fix auth bug",
                },
                context,
            )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "https://github.com/org/repo/pull/99" in result.data
        assert "turns: 5" in result.data.lower()
        mock_push.assert_called_once()

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.shutil.rmtree")
    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset._clone_repo")
    def test_clone_failure_returns_error(self, mock_clone, mock_rmtree, tool, context):
        """Test that clone failure returns a structured error."""
        mock_clone.side_effect = RuntimeError("Failed to clone: not found")

        result = tool._invoke(
            {
                "task": "Fix bug",
                "repo_url": "https://github.com/org/nonexistent",
                "base_branch": "main",
                "pr_title": "Fix",
            },
            context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Failed to clone" in result.error

    def test_get_parameterized_one_liner(self, tool):
        one_liner = tool.get_parameterized_one_liner(
            {"repo_url": "https://github.com/org/repo", "pr_title": "Fix auth"}
        )
        assert "Fix auth" in one_liner
        assert "github.com/org/repo" in one_liner


# ---------------------------------------------------------------------------
# Toolset prerequisite tests
# ---------------------------------------------------------------------------


class TestCodingAgentToolset:
    def test_toolset_default_disabled(self):
        toolset = CodingAgentToolset()
        assert toolset.enabled is False

    def test_toolset_has_correct_name(self):
        toolset = CodingAgentToolset()
        assert toolset.name == "coding_agent"

    def test_toolset_has_one_tool(self):
        toolset = CodingAgentToolset()
        assert len(toolset.tools) == 1
        assert toolset.tools[0].name == "delegate_to_coding_agent"

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_prerequisites_missing_git_token(self, mock_run):
        toolset = CodingAgentToolset()
        ok, msg = toolset.prerequisites_callable({"git_token": ""})
        assert ok is False

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_prerequisites_missing_anthropic_key(self, mock_run):
        toolset = CodingAgentToolset()
        with patch.dict(os.environ, {}, clear=False):
            # Remove ANTHROPIC_API_KEY if present
            env = os.environ.copy()
            env.pop("ANTHROPIC_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                ok, msg = toolset.prerequisites_callable({"git_token": "ghp_tok"})
                assert ok is False
                assert "ANTHROPIC_API_KEY" in msg

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_prerequisites_git_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError("git not found")
        toolset = CodingAgentToolset()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            ok, msg = toolset.prerequisites_callable({"git_token": "ghp_tok"})
            assert ok is False
            assert "git" in msg.lower()

    @patch("holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run")
    def test_prerequisites_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        toolset = CodingAgentToolset()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            ok, msg = toolset.prerequisites_callable({"git_token": "ghp_tok"})
            assert ok is True

    def test_prerequisites_invalid_provider(self):
        toolset = CodingAgentToolset()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with patch(
                "holmes.plugins.toolsets.coding_agent.coding_agent_toolset.subprocess.run"
            ) as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                ok, msg = toolset.prerequisites_callable(
                    {"git_token": "tok", "git_provider_type": "bitbucket"}
                )
                assert ok is False
                assert "Unsupported" in msg
