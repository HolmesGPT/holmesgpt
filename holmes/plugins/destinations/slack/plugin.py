import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import DestinationPlugin


class SlackDestination(DestinationPlugin):
    def __init__(self, token, channel):
        self.token = token
        self.channel = channel
        self.client = WebClient(token=self.token)

    def send_issue(self, issue: Issue, result: LLMResult) -> None:
        color = (
            "#FF0000" if issue.presentation_status == IssueStatus.OPEN else "#00FF00"
        )  # Red for firing, green for resolved
        if issue.presentation_status:
            title = f"{issue.name} - {issue.presentation_status.value}"
        else:
            title = f"{issue.name}"

        if issue.url:
            text = f"*<{issue.url}|{title}>*"
        else:
            text = f"*{title}*"

        blocks = [
            {
                # TODO: consider moving outside of block
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":robot_face: {result.result}",
                },
            }
        ]
        if issue.presentation_key_metadata:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": issue.presentation_key_metadata,
                        }  # type: ignore
                    ],
                }
            )

        try:
            response = self.client.chat_postMessage(
                channel=self.channel,
                text=text,
                attachments=[
                    {
                        "color": color,
                        "blocks": blocks,
                    }
                ],
            )
            self.__send_tool_usage(response["ts"], result)
            self.__send_issue_metadata(response["ts"], issue)
            self.__send_prompt_for_debugging(response["ts"], result)

        except SlackApiError as e:
            if e.response.data["error"] == "channel_not_found":
                logging.error(
                    f"The channel {self.channel} is not found. Please check the value of --slack-channel"
                )
            elif e.response.data["error"] == "invalid_auth":
                logging.error(
                    "Unable to authenticate using the provided Slack token. Please verify the setting of --slack-token"
                )
            else:
                logging.error(f"Error sending message: {e}. message={text}")

    def __send_tool_usage(self, parent_thread, result: LLMResult) -> None:
        if not result.tool_calls:
            return

        text = "*AI used info from alert and the following tools:*"
        for tool in result.tool_calls:
            file_response = self.client.files_upload_v2(
                content=tool.result.get_stringified_data(), title=f"{tool.description}"
            )
            if file_response and "file" in file_response:
                permalink = file_response["file"]["permalink"]
                text += f"\n• `<{permalink}|{tool.description}>`"
            else:
                text += f"\n• {tool.description} (file upload failed)"

        self.client.chat_postMessage(
            channel=self.channel,
            thread_ts=parent_thread,
            text=text,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }
            ],
        )

    def __send_prompt_for_debugging(self, parent_thread, result: LLMResult) -> None:
        if not result.prompt:
            return

        text = "*🐞 DEBUG: messages with OpenAI*"
        file_response = self.client.files_upload_v2(
            content=str(result.prompt) if result.prompt else "", title="ai-prompt"
        )
        if file_response and "file" in file_response:
            permalink = file_response["file"]["permalink"]
            text += f"\n`<{permalink}|ai-prompt>`"
        else:
            text += "\nai-prompt (file upload failed)"

        self.client.chat_postMessage(
            channel=self.channel,
            thread_ts=parent_thread,
            text=text,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }
            ],
        )

    def __send_issue_metadata(self, parent_thread, issue: Issue) -> None:
        if not issue.presentation_all_metadata:
            return

        filename = f"{issue.name}"
        issue_json = issue.model_dump_json()
        file_response = self.client.files_upload_v2(content=issue_json, title=filename)
        if file_response and "file" in file_response:
            permalink = file_response["file"]["permalink"]
            text = issue.presentation_all_metadata
            text += f"\n<{permalink}|{filename}>\n"
        else:
            text = issue.presentation_all_metadata
            text += f"\n{filename} (file upload failed)\n"

        self.client.chat_postMessage(
            channel=self.channel,
            thread_ts=parent_thread,
            text=text,
            unfurl_links=False,
            unfurl_media=True,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "text": f"*{issue.source_type.capitalize()} Metadata*",
                        "type": "mrkdwn",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": text,
                        }
                    ],
                },
            ],
        )
