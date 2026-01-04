import json
import logging
import re
from copy import deepcopy
from typing import Optional

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


def parse_messages_tags(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Parses the user messages for tags and format these.
    System messages and llm responses are ignored and left as-is

    This method returns a shallow copy of the messages list with the exception
    of the messages that have been parsed.
    """
    formatted_messages = []
    for message in messages:
        original_message = message.get("content")
        if message.get("role") == "user" and original_message:
            formatted_str = format_tags_in_string(original_message)
            if formatted_str != message.get("content"):
                formatted_message = deepcopy(message)
                formatted_message["content"] = formatted_str
                formatted_messages.append(formatted_message)
                logging.debug(
                    f"Message with tags '{original_message}' formatted to '{formatted_message}'"
                )
            else:
                formatted_messages.append(message)

        else:
            formatted_messages.append(message)

    return formatted_messages
