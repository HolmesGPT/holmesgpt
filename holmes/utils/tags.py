import json
import logging
import re
from copy import deepcopy
from typing import Any, Optional, Union

from typing_extensions import Dict, List


def stringify_tag(tag: Dict[str, str]) -> Optional[str]:
    """
    This serializes a dictionary into something more readable to the LLM.
    Although I have not seen much difference in quality of output, in theory this can help the LLM
    understand better how to link the tag values with the tools.

    Here are some examples of formatting (more can be found in the test for this function):
        - { "type": "node", "name": "my-node" }
            -> "node my-node"
        - { "type": "issue", "id": "issue-id", "name": "KubeJobFailed", "subject_namespace": "my-namespace", "subject_name": "my-pod" }
            -> issue issue-id (name=KubeJobFailed, subject_namespace=my-namespace, subject_name=my-pod)
    """
    type = tag.pop("type")
    if not type:
        return None

    key = ""
    if tag.get("id"):
        key = tag.pop("id")
    elif tag.get("name"):
        key = tag.pop("name")

    if not key:
        return None

    formatted_string = f"{type} {key}"

    if len(tag) > 0:
        keyVals = []
        for k, v in tag.items():
            keyVals.append(f"{k}={v}")
        formatted_string += f" ({', '.join(keyVals)})"

    return formatted_string


def format_tags_in_string(user_prompt: str) -> str:
    """
    Formats the tags included in a user's message.
    E.g.
        'how many pods are running on << { "type": "node", "name": "my-node" } >>?'
            -> 'how many pods are running on node my-node?'
    """
    try:
        pattern = r"<<(.*?)>>"

        def replace_match(match):
            try:
                json_str = match.group(1)
                json_obj = json.loads(json_str)
                formatted = stringify_tag(json_obj)
                return formatted if formatted else match.group(0)
            except (json.JSONDecodeError, AttributeError):
                logging.warning(f"Failed to parse tag in string: {user_prompt}")
                return match.group(0)

        return re.sub(pattern, replace_match, user_prompt)
    except Exception:
        logging.warning(f"Failed to parse string: {user_prompt}")
        return user_prompt


def _format_content_tags(
    content: Union[str, List[Dict[str, Any]]],
) -> Union[str, List[Dict[str, Any]]]:
    """Format tags in message content, handling both string and multimodal list formats.

    For string content, applies format_tags_in_string directly.
    For multimodal content (list of content blocks), applies tag formatting only
    to text-type blocks while preserving image_url and other block types unchanged.
    """
    if isinstance(content, str):
        return format_tags_in_string(content)
    if isinstance(content, list):
        formatted = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                original_text = block.get("text", "")
                new_text = format_tags_in_string(original_text)
                if new_text != original_text:
                    formatted.append({**block, "text": new_text})
                    changed = True
                else:
                    formatted.append(block)
            else:
                formatted.append(block)
        return formatted if changed else content
    return content


def parse_messages_tags(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Parses the user messages for tags and format these.
    System messages and llm responses are ignored and left as-is.
    Handles both plain string content and multimodal content (list of content blocks
    with text and image_url entries).

    This method returns a shallow copy of the messages list with the exception
    of the messages that have been parsed.
    """
    formatted_messages = []
    for message in messages:
        original_content = message.get("content")
        if message.get("role") == "user" and original_content:
            formatted_content = _format_content_tags(original_content)
            if formatted_content != original_content:
                formatted_message = deepcopy(message)
                formatted_message["content"] = formatted_content
                formatted_messages.append(formatted_message)
                logging.debug(
                    f"Message with tags '{original_content}' formatted to '{formatted_message}'"
                )
            else:
                formatted_messages.append(message)

        else:
            formatted_messages.append(message)

    return formatted_messages
