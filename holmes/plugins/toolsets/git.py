import base64
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests  # type: ignore
from pydantic import BaseModel

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
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class GitHubConfig(BaseModel):
    git_repo: str
    git_credentials: str
    git_branch: str = "main"
    git_url: str = "https://api.github.com"


class GitToolset(Toolset):
    git_repo: Optional[str] = None
    git_url: Optional[str] = None
    git_credentials: Optional[str] = None
    git_branch: Optional[str] = None
    _created_branches: set[str] = set()  # Track branches created by the tool
    _created_prs: set[int] = set()  # Track PRs created by the tool

    def __init__(self):
        super().__init__(
            name="git",
            description="Runs git commands to read repos and create PRs",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/github/",
            icon_url="https://upload.wikimedia.org/wikipedia/commons/9/91/Octicons-mark-github.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                GitReadFileWithLineNumbers(self),
                GitListFiles(self),
                GitListOpenPRs(self),
                GitExecuteChanges(self),
                GitUpdatePR(self),
            ],
            experimental=True,
            tags=[ToolsetTag.CORE],
        )

    def add_created_branch(self, branch_name: str) -> None:
        """Add a branch to the list of branches created by the tool."""
        self._created_branches.add(branch_name)

    def is_created_branch(self, branch_name: str) -> bool:
        """Check if a branch was created by the tool."""
        return branch_name in self._created_branches

    def add_created_pr(self, pr_number: int) -> None:
        """Add a PR to the list of PRs created by the tool."""
        self._created_prs.add(pr_number)

    def is_created_pr(self, pr_number: int) -> bool:
        """Check if a PR was created by the tool."""
        return pr_number in self._created_prs

    def _sanitize_error(self, error_msg: str) -> str:
        """Sanitize error messages by removing sensitive information."""
        if not self.git_credentials:
            return error_msg
        return error_msg.replace(self.git_credentials, "[REDACTED]")

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config and not (os.getenv("GIT_REPO") and os.getenv("GIT_CREDENTIALS")):
            return False, "Missing one or more required Git configuration values."

        try:
            self.git_repo = os.getenv("GIT_REPO") or config.get("git_repo")
            self.git_url = os.getenv("GIT_URL") or config.get("git_url")
            self.git_credentials = os.getenv("GIT_CREDENTIALS") or config.get(
                "git_credentials"
            )
            self.git_branch = os.getenv("GIT_BRANCH") or config.get(
                "git_branch", "main"
            )

            if not all(
                [self.git_repo, self.git_url, self.git_credentials, self.git_branch]
            ):
                logging.error("Missing one or more required Git configuration values.")
                return False, "Missing one or more required Git configuration values."
            return True, ""
        except Exception:
            logging.exception("GitHub prerequisites failed.")
            return False, ""

    def get_example_config(self) -> Dict[str, Any]:
        return {}

    def list_open_prs(self) -> List[Dict[str, Any]]:
        """Helper method to list all open PRs in the repository."""
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/pulls?state=open"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(self._sanitize_error(f"Error listing PRs: {resp.text}"))
        return resp.json()

    def get_branch_ref(self, branch_name: str) -> Optional[str]:
        """Get the SHA of a branch reference."""
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/git/refs/heads/{branch_name}"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise Exception(
                self._sanitize_error(f"Error getting branch reference: {resp.text}")
            )
        return resp.json()["object"]["sha"]

    def create_branch(self, branch_name: str, base_sha: str) -> None:
        """Create a new branch from a base SHA."""
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/git/refs"
        resp = requests.post(
            url,
            headers=headers,
            json={
                "ref": f"refs/heads/{branch_name}",
                "sha": base_sha,
            },
        )
        if resp.status_code not in (200, 201):
            raise Exception(self._sanitize_error(f"Error creating branch: {resp.text}"))
        self.add_created_branch(branch_name)  # Track the created branch

    def get_file_content(self, filepath: str, branch: str) -> tuple[str, str]:
        """Get file content and SHA from a specific branch."""
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/contents/{filepath}?ref={branch}"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 404:
            raise Exception(f"File not found: {filepath}")
        if resp.status_code != 200:
            raise Exception(self._sanitize_error(f"Error fetching file: {resp.text}"))
        file_json = resp.json()
        return file_json["sha"], base64.b64decode(file_json["content"]).decode()

    def update_file(
        self, filepath: str, branch: str, content: str, sha: str, message: str
    ) -> None:
        """Update a file in a specific branch."""
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/contents/{filepath}"
        encoded_content = base64.b64encode(content.encode()).decode()
        resp = requests.put(
            url,
            headers=headers,
            json={
                "message": message,
                "content": encoded_content,
                "branch": branch,
                "sha": sha,
            },
        )
        if resp.status_code not in (200, 201):
            raise Exception(self._sanitize_error(f"Error updating file: {resp.text}"))

    def create_pr(self, title: str, head: str, base: str, body: str) -> str:
        """Create a new pull request."""
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/pulls"
        resp = requests.post(
            url,
            headers=headers,
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        if resp.status_code not in (200, 201):
            raise Exception(self._sanitize_error(f"Error creating PR: {resp.text}"))
        pr_number = resp.json()["number"]
        self.add_created_pr(pr_number)  # Track the created PR
        return resp.json()["html_url"]

    def get_pr_details(self, pr_number: int) -> Dict[str, Any]:
        """Get details of a specific PR."""
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/pulls/{pr_number}"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(
                self._sanitize_error(f"Error getting PR details: {resp.text}")
            )
        return resp.json()

    def get_pr_branch(self, pr_number: int) -> str:
        """Get the branch name for a specific PR."""
        pr_details = self.get_pr_details(pr_number)
        return pr_details["head"]["ref"]

    def add_commit_to_pr(
        self, pr_number: int, filepath: str, content: str, message: str
    ) -> None:
        """Add a commit to an existing PR's branch."""
        branch = self.get_pr_branch(pr_number)
        try:
            # Get current file content and SHA
            sha, _ = self.get_file_content(filepath, branch)
        except Exception:
            # File might not exist yet, that's okay
            sha = None

        # Update file
        headers = {"Authorization": f"token {self.git_credentials}"}
        url = f"{self.git_url}/repos/{self.git_repo}/contents/{filepath}"
        encoded_content = base64.b64encode(content.encode()).decode()
        data = {
            "message": message,
            "content": encoded_content,
            "branch": branch,
        }
        if sha:
            data["sha"] = sha

        resp = requests.put(url, headers=headers, json=data)
        if resp.status_code not in (200, 201):
            raise Exception(
                self._sanitize_error(f"Error adding commit to PR: {resp.text}")
            )


class GitReadFileWithLineNumbers(Tool):
    toolset: GitToolset

    def __init__(self, toolset: GitToolset):
        super().__init__(
            name="git_read_file_with_line_numbers",
            description="Reads a file from the Git repo and prints each line with line numbers",
            parameters={
                "filepath": ToolParameter(
                    description="The path of the file in the repository to read.",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def _invoke(
        self,
        params: dict,
        context: ToolInvokeContext,
    ) -> StructuredToolResult:
        filepath = params["filepath"]
        try:
            headers = {"Authorization": f"token {self.toolset.git_credentials}"}
            url = f"{self.toolset.git_url}/repos/{self.toolset.git_repo}/contents/{filepath}"
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    data=self.toolset._sanitize_error(
                        f"Error fetching file: {resp.text}"
                    ),
                    params=params,
                )
            content = base64.b64decode(resp.json()["content"]).decode().splitlines()
            numbered = "\n".join(f"{i+1}: {line}" for i, line in enumerate(content))
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=numbered,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=self.toolset._sanitize_error(str(e)),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        filepath = params.get("filepath", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Read Git File ({filepath})"


class GitListFiles(Tool):
    toolset: GitToolset

    def __init__(self, toolset: GitToolset):
        super().__init__(
            name="git_list_files",
            description="Lists all files and directories in the remote Git repository.",
            parameters={},
            toolset=toolset,  # type: ignore
        )

    def _invoke(
        self,
        params: dict,
        context: ToolInvokeContext,
    ) -> StructuredToolResult:
        try:
            headers = {"Authorization": f"token {self.toolset.git_credentials}"}
            url = f"{self.toolset.git_url}/repos/{self.toolset.git_repo}/git/trees/{self.toolset.git_branch}?recursive=1"
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    data=self.toolset._sanitize_error(
                        f"Error listing files: {resp.text}"
                    ),
                    params=params,
                )
            paths = [entry["path"] for entry in resp.json()["tree"]]
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=paths,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=self.toolset._sanitize_error(str(e)),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List Git Files"


class GitListOpenPRs(Tool):
    toolset: GitToolset

    def __init__(self, toolset: GitToolset):
        super().__init__(
            name="git_list_open_prs",
            description="Lists all open pull requests (PRs) in the remote Git repository.",
            parameters={},
            toolset=toolset,  # type: ignore
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            prs = self.toolset.list_open_prs()
            formatted = [
                {
                    "number": pr["number"],
                    "title": pr["title"],
                    "branch": pr["head"]["ref"],
                    "url": pr["html_url"],
                }
                for pr in prs
            ]
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=formatted,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=self.toolset._sanitize_error(str(e)),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List Open PRs"


class GitExecuteChanges(Tool):
    toolset: GitToolset

    def __init__(self, toolset: GitToolset):
        super().__init__(
            name="git_execute_changes",
            description="Make changes to a GitHub file and optionally open a PR or add to existing PR. This tool requires two steps: first run with dry_run=true to preview changes, then run again with dry_run=false to commit the changes.",
            parameters={
                "line": ToolParameter(
                    description="Line number to change", type="integer", required=True
                ),
                "filename": ToolParameter(
                    description="Filename (relative path)", type="string", required=True
                ),
                "command": ToolParameter(
                    description="insert/update/remove", type="string", required=True
                ),
                "code": ToolParameter(
                    description="The entire line of code to insert or update",
                    type="string",
                    required=False,
                ),
                "open_pr": ToolParameter(
                    description="Whether to open PR", type="boolean", required=True
                ),
                "commit_pr": ToolParameter(
                    description="PR title or PR number to add commit to",
                    type="string",
                    required=True,
                ),
                "dry_run": ToolParameter(
                    description="Dry-run mode", type="boolean", required=True
                ),
                "commit_message": ToolParameter(
                    description="Commit message", type="string", required=True
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        def error(msg: str) -> StructuredToolResult:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=self.toolset._sanitize_error(msg),
                params=params,
            )

        def success(msg: Any) -> StructuredToolResult:
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS, data=msg, params=params
            )

        def modify_lines(lines: List[str]) -> List[str]:
            nonlocal command, line, code  # type: ignore
            if command == "insert":
                prev_line = lines[line - 2] if line > 1 else ""
                prev_indent = len(prev_line) - len(prev_line.lstrip())
                indent = (
                    prev_indent + 2 if prev_line.rstrip().endswith(":") else prev_indent
                )
                for i in range(line - 1, len(lines)):
                    if lines[i].strip():
                        next_indent = len(lines[i]) - len(lines[i].lstrip())
                        if next_indent > prev_indent:
                            indent = next_indent
                        break
                lines.insert(line - 1, " " * indent + code.lstrip())
            elif command == "update":
                indent = len(lines[line - 1]) - len(lines[line - 1].lstrip())
                lines[line - 1] = " " * indent + code.lstrip()
            elif command == "remove":
                del lines[line - 1]
            else:
                raise ValueError(f"Invalid command: {command}")
            return lines

        try:
            line = params["line"]
            filename = params["filename"]
            command = params["command"]
            code = params.get("code", "")
            open_pr = params["open_pr"]
            commit_pr = params["commit_pr"]
            dry_run = params["dry_run"]
            commit_message = params["commit_message"]
            branch = self.toolset.git_branch

            if not commit_message.strip():
                return error("Commit message cannot be empty")
            if not filename.strip():
                return error("Filename cannot be empty")
            if line < 1:
                return error("Line number must be positive")

            # Handle updating an existing PR
            if commit_pr.startswith("#") or commit_pr.isdigit():
                try:
                    pr_number = int(commit_pr.lstrip("#"))
                    branch = self.toolset.get_pr_branch(pr_number)
                    sha, content = self.toolset.get_file_content(filename, branch)
                    updated_lines = modify_lines(content.splitlines())
                    updated_content = "\n".join(updated_lines) + "\n"
                    if dry_run:
                        return success(
                            f"DRY RUN: Updated content for PR #{pr_number}:\n\n{updated_content}"
                        )
                    self.toolset.add_commit_to_pr(
                        pr_number, filename, updated_content, commit_message
                    )
                    return success(f"Added commit to PR #{pr_number} successfully")
                except Exception as e:
                    return error(f"Error updating PR: {e}")

            # Handle creating a new PR
            pr_name = commit_pr.replace(" ", "_").replace("'", "")
            branch_name = f"feature/{pr_name}"
            if not commit_pr.strip():
                return error("PR title cannot be empty")

            if self.toolset.get_branch_ref(
                branch_name
            ) and not self.toolset.is_created_branch(branch_name):
                return error(
                    f"Branch {branch_name} already exists. Please use a different PR title or manually delete it."
                )

            # Reuse existing PR if matched
            if open_pr:
                try:
                    for pr in self.toolset.list_open_prs():
                        if (
                            pr["title"].lower() == commit_pr.lower()
                            and pr["head"]["ref"] == branch_name
                        ):
                            if not self.toolset.is_created_pr(pr["number"]):
                                return error(
                                    f"PR #{pr['number']} was not created by this tool."
                                )
                            branch = self.toolset.get_pr_branch(pr["number"])
                            sha, content = self.toolset.get_file_content(
                                filename, branch
                            )
                            updated_lines = modify_lines(content.splitlines())
                            updated_content = "\n".join(updated_lines) + "\n"
                            if dry_run:
                                return success(
                                    f"DRY RUN: Updated content for PR #{pr['number']}:\n\n{updated_content}"
                                )
                            self.toolset.add_commit_to_pr(
                                pr["number"], filename, updated_content, commit_message
                            )
                            return success(
                                f"Added commit to PR #{pr['number']} successfully"
                            )
                except Exception as e:
                    return error(f"Error checking existing PRs: {e}")

            try:
                base_sha = self.toolset.get_branch_ref(branch)  # type: ignore
                if not base_sha:
                    return error(f"Base branch {branch} not found")
            except Exception as e:
                return error(f"Error getting base branch reference: {e}")

            try:
                sha, content = self.toolset.get_file_content(filename, branch)  # type: ignore
                lines = content.splitlines()
            except Exception as e:
                return error(f"Error getting file content: {e}")

            if line > len(lines) + 1:
                return error(
                    f"Line number {line} is out of range. File has {len(lines)} lines."
                )

            updated_lines = modify_lines(lines)
            updated_content = "\n".join(updated_lines) + "\n"

            if dry_run:
                return success(f"DRY RUN: Updated content:\n\n{updated_content}")

            try:
                self.toolset.create_branch(branch_name, base_sha)
                self.toolset.update_file(
                    filename, branch_name, updated_content, sha, commit_message
                )
            except Exception as e:
                return error(f"Error during branch creation or file update: {e}")

            if open_pr:
                try:
                    pr_url = self.toolset.create_pr(
                        commit_pr,
                        branch_name,
                        branch,  # type: ignore
                        commit_message,  # type: ignore
                    )
                    return success(f"PR opened successfully: {pr_url}")
                except Exception as e:
                    return error(
                        f"PR creation failed. Branch created and committed successfully. Error: {e}"
                    )

            return success("Change committed successfully, no PR opened.")
        except Exception as e:
            return error(f"Unexpected error: {e}")

    def get_parameterized_one_liner(self, params) -> str:
        command = params.get("command", "")
        filename = params.get("filename", "")
        dry_run = params.get("dry_run", False)
        mode = "(dry run)" if dry_run else ""
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Execute Git Changes ({command} in {filename}) {mode}".strip()


class GitUpdatePR(Tool):
    """A tool specifically for updating existing PRs that were created by this tool.
    This tool can only update PRs that were created using the GitExecuteChanges tool,
    as it relies on the specific branch naming convention used by that tool.
    The tool requires two steps: first run with dry_run=true to preview changes,
    then run again with dry_run=false to commit the changes to the PR.
    """

    toolset: GitToolset

    def __init__(self, toolset: GitToolset):
        super().__init__(
            name="git_update_pr",
            description="Update an existing PR that was created by this tool. Can only update PRs created using git_execute_changes.",
            parameters={
                "line": ToolParameter(
                    description="Line number to change", type="integer", required=True
                ),
                "filename": ToolParameter(
                    description="Filename (relative path)", type="string", required=True
                ),
                "command": ToolParameter(
                    description="insert/update/remove", type="string", required=True
                ),
                "code": ToolParameter(
                    description="The entire line of code to insert or update",
                    type="string",
                    required=False,
                ),
                "pr_number": ToolParameter(
                    description="PR number to update", type="integer", required=True
                ),
                "dry_run": ToolParameter(
                    description="Dry-run mode", type="boolean", required=True
                ),
                "commit_message": ToolParameter(
                    description="Commit message", type="string", required=True
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            line = params["line"]
            filename = params["filename"]
            command = params["command"]
            code = params.get("code", "")
            pr_number = params["pr_number"]
            dry_run = params["dry_run"]
            commit_message = params["commit_message"]

            # Validate inputs
            if not commit_message.strip():
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Tool call failed to run: Commit message cannot be empty",
                )
            if not filename.strip():
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Tool call failed to run: Filename cannot be empty",
                )
            if line < 1:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Tool call failed to run: Line number must be positive",
                )

            # Verify this is a PR created by our tool
            if not self.toolset.is_created_pr(pr_number):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Tool call failed to run: PR #{pr_number} was not created by this tool. Only PRs created using git_execute_changes can be updated.",
                )

            # Get PR details
            try:
                pr_details = self.toolset.get_pr_details(pr_number)
                branch = pr_details["head"]["ref"]

                # Get current file content from PR branch
                sha, content = self.toolset.get_file_content(filename, branch)
                content_lines = content.splitlines()

                # Update content
                if command == "insert":
                    # Get the previous line's indentation
                    prev_line = content_lines[line - 2] if line > 1 else ""
                    prev_indent = len(prev_line) - len(prev_line.lstrip())

                    # If previous line ends with colon, add extra indentation
                    if prev_line.rstrip().endswith(":"):
                        # Find the next non-empty line to determine proper indentation
                        next_line_idx = line - 1
                        while (
                            next_line_idx < len(content_lines)
                            and not content_lines[next_line_idx].strip()
                        ):
                            next_line_idx += 1

                        if next_line_idx < len(content_lines):
                            next_line = content_lines[next_line_idx]
                            next_indent = len(next_line) - len(next_line.lstrip())
                            # Use the next line's indentation if it's more indented than current
                            if next_indent > prev_indent:
                                indent = next_indent
                            else:
                                indent = prev_indent + 2
                        else:
                            indent = prev_indent + 2
                    else:
                        indent = prev_indent

                    # Apply indentation to the new line
                    indented_code = " " * indent + code.lstrip()
                    content_lines.insert(line - 1, indented_code)
                elif command == "update":
                    indent = len(content_lines[line - 1]) - len(
                        content_lines[line - 1].lstrip()
                    )
                    content_lines[line - 1] = " " * indent + code.lstrip()
                elif command == "remove":
                    del content_lines[line - 1]
                else:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=f"Tool call failed to run: Invalid command: {command}",
                    )

                updated_content = "\n".join(content_lines) + "\n"

                if dry_run:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS,
                        data=f"DRY RUN: Updated content for PR #{pr_number}:\n\n{updated_content}",
                    )

                # Add commit to PR
                self.toolset.add_commit_to_pr(
                    pr_number, filename, updated_content, commit_message
                )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=f"Added commit to PR #{pr_number} successfully",
                )

            except Exception as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=self.toolset._sanitize_error(
                        f"Tool call failed to run: Error updating PR: {str(e)}"
                    ),
                )

        except requests.exceptions.RequestException as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=self.toolset._sanitize_error(
                    f"Tool call failed to run: Network error: {str(e)}"
                ),
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=self.toolset._sanitize_error(
                    f"Tool call failed to run: Unexpected error: {str(e)}"
                ),
            )

    def get_parameterized_one_liner(self, params) -> str:
        pr_number = params.get("pr_number", "")
        command = params.get("command", "")
        dry_run = params.get("dry_run", False)
        mode = "(dry run)" if dry_run else ""
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Update PR #{pr_number} ({command}) {mode}".strip()
