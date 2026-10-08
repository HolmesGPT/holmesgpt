import json
import logging
import mimetypes
from typing import List, Optional
from urllib.parse import urljoin

import requests  # type:ignore

from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import DestinationPlugin


class MattermostDestination(DestinationPlugin):
    """Mattermost destination plugin for posting investigation results."""

    def __init__(
        self,
        url: str,
        token: str,
        channel_id: str,
        verify_ssl: bool = True,
    ) -> None:
        """
        Initialize the Mattermost destination.

        Args:
            url: Mattermost server base URL (e.g. https://mattermost.example.com)
            token: Mattermost bot or personal access token used for ``Authorization: Bearer``
            channel_id: Mattermost channel ID (26-character alphanumeric string).
                Not the ``#channel-name`` slug. Look it up in the Mattermost UI
                under *Channel Info → Channel ID* or via ``/api/v4/teams/{team}/channels/name/{name}``.
            verify_ssl: Whether to verify the server's TLS certificate.
        """
        self.base_url = url.rstrip("/")
        self.token = token
        self.channel_id = channel_id
        self.verify_ssl = verify_ssl
        self._headers = {"Authorization": f"Bearer {token}"}

    def send_issue(self, issue: Issue, result: LLMResult) -> None:
        # Red for firing, green for resolved, gray when the status is unknown.
        if issue.presentation_status == IssueStatus.OPEN:
            color = "#FF0000"
        elif issue.presentation_status == IssueStatus.CLOSED:
            color = "#00FF00"
        else:
            color = "#808080"
        if issue.presentation_status and issue.show_status_in_title:
            title = f"{issue.name} - {issue.presentation_status.value}"
        else:
            title = issue.name

        if issue.url:
            pretext = f"**[{title}]({issue.url})**"
        else:
            pretext = f"**{title}**"

        attachment: dict = {
            "color": color,
            "pretext": pretext,
            "text": f":robot_face: {result.result}",
        }
        if issue.presentation_key_metadata:
            attachment["footer"] = issue.presentation_key_metadata

        try:
            response = self._post_message(message="", attachments=[attachment])
        except requests.exceptions.RequestException as e:
            self._log_send_error(e, title)
            raise

        root_id = response.get("id")
        if not root_id:
            logging.error(
                "Mattermost returned a response without a post id; skipping thread replies."
            )
            return

        try:
            self.__send_tool_usage(root_id, result)
            self.__send_issue_metadata(root_id, issue)
            self.__send_prompt_for_debugging(root_id, result)
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed while posting Mattermost thread replies: {e}")

    def __send_tool_usage(self, parent_id: str, result: LLMResult) -> None:
        if not result.tool_calls:
            return

        lines: list[str] = [
            "**AI used info from alert and the following tools:**",
        ]
        file_ids: list[str] = []
        for tool in result.tool_calls:
            file_id = self._upload_file(
                filename=f"{self._safe_filename(tool.description)}.txt",
                content=tool.result.get_stringified_data(),
            )
            if file_id:
                file_ids.append(file_id)
                lines.append(f"- `{tool.description}`")
            else:
                lines.append(f"- {tool.description} (file upload failed)")

        self._post_message(
            message="\n".join(lines),
            root_id=parent_id,
            file_ids=file_ids or None,
        )

    def __send_issue_metadata(self, parent_id: str, issue: Issue) -> None:
        if not issue.presentation_all_metadata:
            return

        filename = f"{self._safe_filename(issue.name)}.json"
        file_id = self._upload_file(filename=filename, content=issue.model_dump_json())

        text = f"**{issue.source_type.capitalize()} Metadata**\n{issue.presentation_all_metadata}"
        if not file_id:
            text += f"\n{filename} (file upload failed)"

        self._post_message(
            message=text,
            root_id=parent_id,
            file_ids=[file_id] if file_id else None,
        )

    def __send_prompt_for_debugging(self, parent_id: str, result: LLMResult) -> None:
        if not result.messages:
            return

        file_id = self._upload_file(
            filename="ai-prompt.json",
            content=json.dumps(result.messages, indent=2),
        )
        text = "**🐞 DEBUG: messages with LLM**"
        if not file_id:
            text += "\nai-prompt (file upload failed)"

        self._post_message(
            message=text,
            root_id=parent_id,
            file_ids=[file_id] if file_id else None,
        )

    def _post_message(
        self,
        message: str,
        attachments: Optional[list] = None,
        root_id: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
    ) -> dict:
        payload: dict = {"channel_id": self.channel_id, "message": message}
        if attachments:
            payload["props"] = {"attachments": attachments}
        if root_id:
            payload["root_id"] = root_id
        if file_ids:
            payload["file_ids"] = file_ids

        response = requests.post(
            urljoin(self.base_url + "/", "api/v4/posts"),
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        return response.json()

    def _upload_file(self, filename: str, content: str) -> Optional[str]:
        try:
            mime_type = mimetypes.guess_type(filename)[0] or "text/plain"
            response = requests.post(
                urljoin(self.base_url + "/", "api/v4/files"),
                headers=self._headers,
                data={"channel_id": self.channel_id},
                files={"files": (filename, content.encode("utf-8"), mime_type)},
                timeout=60,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            file_infos = response.json().get("file_infos") or []
            if file_infos:
                return file_infos[0].get("id")
            return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Mattermost file upload failed for {filename}: {e}")
            return None

    @staticmethod
    def _safe_filename(name: str) -> str:
        cleaned = "".join(
            c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name
        )
        return cleaned[:200] or "file"

    @staticmethod
    def _log_send_error(
        error: requests.exceptions.RequestException, title: str
    ) -> None:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status == 401:
            logging.error(
                "Mattermost authentication failed (401). Check --mattermost-token."
            )
        elif status == 403:
            logging.error(
                "Mattermost authorization failed (403). The bot may not have access to the target channel."
            )
        elif status == 404:
            logging.error(
                "Mattermost channel not found (404). Verify --mattermost-channel-id."
            )
        else:
            logging.error(f"Error sending Mattermost message: {error}. title={title}")
