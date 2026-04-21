"""
Coding agent delegation toolset.

Delegates code changes to an external coding agent (Claude Code) that can
check out a git repository, apply fixes, and open a pull request.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.coding_agent.git_provider import (
    GitProvider,
    create_git_provider,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

# Default tools the coding agent is allowed to use
DEFAULT_ALLOWED_TOOLS: List[str] = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash(git *)",
    "Bash(cd *)",
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(find *)",
    "Bash(head *)",
    "Bash(tail *)",
    "Bash(wc *)",
    "Bash(diff *)",
    "Bash(python *)",
    "Bash(pip *)",
    "Bash(npm *)",
    "Bash(make *)",
    "Bash(pytest *)",
]

# Tools that are always blocked
DEFAULT_DISALLOWED_TOOLS: List[str] = [
    "Bash(rm -rf /)",
    "Bash(sudo *)",
    "Bash(curl *)",
    "Bash(wget *)",
    "Bash(docker *)",
    "Bash(kubectl *)",
]


class CodingAgentConfig(ToolsetConfig):
    """Configuration for the coding agent delegation toolset."""

    git_provider_type: str = Field(
        default="github",
        title="Git Provider",
        description="Git hosting provider: 'github' or 'gitlab'.",
        examples=["github", "gitlab"],
    )
    git_token: str = Field(
        title="Git Token",
        description="Personal access token for git operations (clone + create PR). "
        "For GitHub: a PAT with 'repo' scope. "
        "For GitLab: a PAT with 'api' scope.",
        examples=["ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"],
    )
    max_turns: int = Field(
        default=30,
        title="Max Turns",
        description="Maximum number of agentic turns the coding agent can take.",
    )
    allowed_tools: Optional[List[str]] = Field(
        default=None,
        title="Allowed Tools",
        description="Tools the coding agent is allowed to use. "
        "If not set, a safe default list is used.",
    )
    disallowed_tools: Optional[List[str]] = Field(
        default=None,
        title="Disallowed Tools",
        description="Tools the coding agent is explicitly blocked from using. "
        "If not set, a safe default list is used.",
    )
    model: Optional[str] = Field(
        default=None,
        title="Model",
        description="Model for the coding agent to use. "
        "If not set, Claude Code uses its default model. "
        "Requires ANTHROPIC_API_KEY in the environment.",
    )
    draft_pr: bool = Field(
        default=True,
        title="Draft PR",
        description="Create the PR as a draft.",
    )
    timeout_seconds: int = Field(
        default=600,
        title="Timeout",
        description="Maximum time in seconds for the coding agent to complete.",
    )


def _clone_repo(repo_url: str, base_branch: str, git_provider: GitProvider) -> str:
    """Clone a repository into a temporary directory.

    Returns the path to the cloned repo.
    """
    workdir = tempfile.mkdtemp(prefix="holmes-coding-agent-")
    authed_url = git_provider.get_authenticated_clone_url(repo_url)

    try:
        subprocess.run(
            ["git", "clone", "--depth=1", "-b", base_branch, authed_url, workdir],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError(
            f"Failed to clone {repo_url} (branch: {base_branch}): {e.stderr}"
        ) from e
    except subprocess.TimeoutExpired:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError(f"Clone of {repo_url} timed out after 120 seconds")

    return workdir


def _create_branch(workdir: str, branch_name: str) -> None:
    """Create and checkout a new branch in the repo."""
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
    )


def _has_changes(workdir: str) -> bool:
    """Check if the working directory has any uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _commit_and_push(
    workdir: str,
    branch_name: str,
    commit_message: str,
    git_provider: GitProvider,
    repo_url: str,
) -> None:
    """Stage all changes, commit, and push to the remote."""
    subprocess.run(
        ["git", "add", "-A"],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
    )

    authed_url = git_provider.get_authenticated_clone_url(repo_url)
    subprocess.run(
        ["git", "remote", "set-url", "origin", authed_url],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


async def _run_coding_agent(
    workdir: str,
    task_prompt: str,
    config: CodingAgentConfig,
) -> Tuple[str, int, Optional[float]]:
    """Run the Claude Code agent via the SDK and return (result_text, turns, cost).

    Raises RuntimeError on failure.
    """
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            CLINotFoundError,
            ClaudeAgentOptions,
            ProcessError,
            ResultMessage,
            TextBlock,
            query,
        )
    except ImportError as e:
        raise RuntimeError(
            "claude-agent-sdk is not installed. "
            "Install it with: pip install claude-agent-sdk"
        ) from e

    allowed = config.allowed_tools or DEFAULT_ALLOWED_TOOLS
    disallowed = config.disallowed_tools or DEFAULT_DISALLOWED_TOOLS

    options = ClaudeAgentOptions(
        cwd=workdir,
        allowed_tools=allowed,
        disallowed_tools=disallowed,
        max_turns=config.max_turns,
        permission_mode="bypassPermissions",
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": (
                "You are working in a git repository. Your job is to make the requested "
                "code changes. Do NOT create a pull request or push — just make the "
                "changes and commit them locally. Stay within the repository directory."
            ),
        },
        model=config.model,
    )

    result_text = ""
    num_turns = 0
    cost: Optional[float] = None

    try:
        async for message in query(prompt=task_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text + "\n"
            elif isinstance(message, ResultMessage):
                num_turns = message.num_turns
                cost = message.total_cost_usd
                if message.result:
                    result_text = message.result
                if message.is_error:
                    raise RuntimeError(
                        f"Coding agent failed after {num_turns} turns: {result_text}"
                    )
    except CLINotFoundError:
        raise RuntimeError(
            "Claude Code CLI not found. The claude-agent-sdk package must be installed "
            "and the Claude Code CLI must be available in PATH."
        )
    except ProcessError as e:
        raise RuntimeError(f"Coding agent process error (exit code {e.exit_code}): {e}")

    return result_text, num_turns, cost


class DelegateToAgentTool(Tool):
    """Tool that delegates a coding task to an external coding agent."""

    toolset: "CodingAgentToolset"

    def __init__(self, toolset: "CodingAgentToolset"):
        super().__init__(
            name="delegate_to_coding_agent",
            description=(
                "Delegate a code change task to an autonomous coding agent. "
                "The agent will clone the repository, make the requested changes, "
                "and open a pull request. Use this when you've identified a code fix "
                "that should be applied to a repository. "
                "Returns the PR URL and a summary of changes."
            ),
            parameters={
                "task": ToolParameter(
                    description=(
                        "Detailed description of the code changes to make. "
                        "Include the problem context from your investigation, "
                        "which files are likely involved, and what the fix should be."
                    ),
                    type="string",
                    required=True,
                ),
                "repo_url": ToolParameter(
                    description=(
                        "HTTPS URL of the git repository to clone. "
                        "Example: https://github.com/org/repo or https://gitlab.com/org/repo"
                    ),
                    type="string",
                    required=True,
                ),
                "base_branch": ToolParameter(
                    description="Branch to base the changes on (e.g. 'main', 'master').",
                    type="string",
                    required=True,
                ),
                "pr_title": ToolParameter(
                    description="Title for the pull request.",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore[call-arg]
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        task = params.get("task", "")
        repo_url = params.get("repo_url", "")
        base_branch = params.get("base_branch", "main")
        pr_title = params.get("pr_title", "Holmes auto-fix")

        if not task:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'task' parameter is required.",
                params=params,
            )
        if not repo_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'repo_url' parameter is required.",
                params=params,
            )

        config: CodingAgentConfig = self.toolset.coding_agent_config
        git_provider = self.toolset._git_provider
        if not git_provider:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Git provider not initialized. Check toolset configuration.",
                params=params,
            )

        branch_name = f"holmes/fix-{uuid.uuid4().hex[:8]}"
        workdir: Optional[str] = None

        try:
            # 1. Clone the repository
            logger.info(f"Cloning {repo_url} (branch: {base_branch})")
            workdir = _clone_repo(repo_url, base_branch, git_provider)

            # 2. Create a feature branch
            _create_branch(workdir, branch_name)

            # 3. Run the coding agent
            logger.info(f"Running coding agent in {workdir}")
            agent_result, num_turns, cost = asyncio.run(
                _run_coding_agent(workdir, task, config)
            )

            # 4. Check for changes
            if not _has_changes(workdir):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data="The coding agent did not make any changes to the repository.",
                    params=params,
                )

            # 5. Commit and push
            commit_message = (
                f"fix: {pr_title}\n\nAutomated fix by Holmes + coding agent."
            )
            _commit_and_push(
                workdir, branch_name, commit_message, git_provider, repo_url
            )

            # 6. Create pull request
            pr_body = (
                f"## Automated Fix\n\n"
                f"This PR was created automatically by Holmes after investigating an issue.\n\n"
                f"### Task\n{task}\n\n"
                f"### Agent Summary\n{agent_result[:2000]}\n"
            )
            pr_url = git_provider.create_pull_request(
                repo_url=repo_url,
                head_branch=branch_name,
                base_branch=base_branch,
                title=pr_title,
                body=pr_body,
                draft=config.draft_pr,
            )

            cost_str = f"${cost:.4f}" if cost else "unknown"
            summary = (
                f"Pull request created: {pr_url}\n"
                f"Branch: {branch_name}\n"
                f"Agent turns: {num_turns}, Cost: {cost_str}\n\n"
                f"Agent summary:\n{agent_result[:1000]}"
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=summary,
                params=params,
                invocation=f"delegate_to_coding_agent(repo={repo_url}, branch={base_branch})",
            )

        except RuntimeError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
                invocation=f"delegate_to_coding_agent(repo={repo_url}, branch={base_branch})",
            )
        except Exception as e:
            logger.exception("Unexpected error in coding agent delegation")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {type(e).__name__}: {e}",
                params=params,
                invocation=f"delegate_to_coding_agent(repo={repo_url}, branch={base_branch})",
            )
        finally:
            if workdir:
                shutil.rmtree(workdir, ignore_errors=True)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        repo = params.get("repo_url", "unknown")
        title = params.get("pr_title", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: {title} → {repo}"


class CodingAgentToolset(Toolset):
    """Toolset that delegates code changes to an autonomous coding agent."""

    config_classes: ClassVar[list[Type[CodingAgentConfig]]] = [CodingAgentConfig]
    config: Optional[CodingAgentConfig] = None
    _git_provider: Optional[GitProvider] = None

    def __init__(self):
        super().__init__(
            name="coding_agent",
            enabled=False,  # Opt-in: requires explicit configuration
            description=(
                "Delegates code changes to an autonomous coding agent (Claude Code) "
                "that can clone repositories, apply fixes, and open pull requests."
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/coding-agent/",
            icon_url="https://cdn-icons-png.flaticon.com/512/2092/2092663.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[DelegateToAgentTool(self)],
            tags=[ToolsetTag.CORE],
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    @property
    def coding_agent_config(self) -> CodingAgentConfig:
        if self.config is None:
            raise RuntimeError("CodingAgentToolset config not initialized")
        return self.config  # type: ignore[return-value]

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = CodingAgentConfig(**config)
        except Exception as e:
            return False, f"Invalid coding agent config: {e}"

        # Verify git token is set
        if not self.config.git_token:
            return False, "git_token is required for the coding agent toolset."

        # Verify Claude Agent SDK is installed
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return (
                False,
                "claude-agent-sdk is not installed. "
                "Install it with: pip install claude-agent-sdk",
            )

        # Verify ANTHROPIC_API_KEY is set
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return (
                False,
                "ANTHROPIC_API_KEY environment variable is required for the coding agent.",
            )

        # Verify git is available
        try:
            subprocess.run(
                ["git", "--version"],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False, "git is not installed or not in PATH."

        # Create git provider
        try:
            self._git_provider = create_git_provider(
                provider_type=self.config.git_provider_type,
                token=self.config.git_token,
            )
        except ValueError as e:
            return False, str(e)

        return True, "Coding agent toolset is ready."
