import logging
from typing import Any, Dict, Optional, Tuple
import json
import re
from contextlib import suppress
from holmes.common.env_vars import load_bool


REQUEST_STRUCTURED_OUTPUT_FROM_LLM = load_bool(
    "REQUEST_STRUCTURED_OUTPUT_FROM_LLM", True
)
PARSE_INVESTIGATION_MARKDOWN_INTO_STRUCTURED_SECTIONS = load_bool(
    "PARSE_INVESTIGATION_MARKDOWN_INTO_STRUCTURED_SECTIONS", True
)


InputSectionsDataType = Dict[str, str]

DEFAULT_SECTIONS: InputSectionsDataType = {
    "Alert Explanation": '1-2 sentences explaining the alert itself - note don\'t say "The alert indicates a warning event related to a Kubernetes pod doing blah" rather just say "The pod XYZ did blah" because that is what the user actually cares about',
    "Key Findings": "What you checked and found",
    "Conclusions and Possible Root causes": "What conclusions can you reach based on the data you found? what are possible root causes (if you have enough conviction to say) or what uncertainty remains. Don't say root cause but 'possible root causes'. Be clear to distinguish between what you know for certain and what is a possible explanation",
    "Next Steps": "What you would do next to troubleshoot this issue, any commands that could be run to fix it, or other ways to solve it (prefer giving precise bash commands when possible)",
    "Related logs": "Truncate and share the most relevant logs, especially if these explain the root cause. For example: \nLogs from pod robusta-holmes:\n```\n<logs>```\n. Always embed the surroundding +/- 5 log lines to any relevant logs. ",
    "App or Infra?": "Explain whether the issue is more likely an infrastructure or an application level issue and why you think that.",
    "External links": "Provide links to external sources and a short sentence describing each link. For example provide links to relevant runbooks, etc. This section is a markdown formatted string.",
}


def get_output_format_for_investigation(
    sections: InputSectionsDataType,
) -> Dict[str, Any]:
    properties = {}
    required_fields = []

    for title, description in sections.items():
        properties[title] = {"type": ["string", "null"], "description": description}
        required_fields.append(title)

    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": required_fields,
        "properties": properties,
        "additionalProperties": False,
    }
    output_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "InvestigationResult",
            "schema": schema,
            "strict": False,
        },
    }

    return output_format


def combine_sections(sections: Dict) -> str:
    content = ""
    for section_title, section_content in sections.items():
        if section_content:
            content = content + f"\n# {section_title}\n{section_content}\n"
    return content


def parse_markdown_into_sections_from_equal_sign(
    markdown_content: str,
) -> Optional[Dict[str, Optional[str]]]:
    """Splits a markdown in different sections where the key is a top level title underlined with `====` and the value is the content
    ```
    Header Title
    ===========
    Content here
    ```
    =>
    {
      "Header Title": "Content here"
    }
    """
    matches = re.split(r"(?:^|\n)([^\n]+)\n=+\n", markdown_content.strip())

    # Remove any empty first element if the text starts with a header
    if matches[0].strip() == "":
        matches = matches[1:]

    sections = {}

    for i in range(0, len(matches), 2):
        if i + 1 < len(matches):
            header = matches[i]
            content = matches[i + 1].strip()
            sections[header] = content

    if len(sections) > 0:
        return sections
    else:
        return None


def parse_markdown_into_sections_from_hash_sign(
    markdown_content: str,
) -> Optional[Dict[str, Optional[str]]]:
    """Splits a markdown in different sections where the key is a top level title underlined with `====` and the value is the content
    ```
    # Header Title
    Content here
    ```
    =>
    {
      "Header Title": "Content here"
    }
    """
    # Split the text into sections based on headers (# Section)
    matches = re.split(r"\n(?=# )", markdown_content.strip())

    if not matches[0].startswith("#"):
        matches = matches[1:]

    sections = {}

    for match in matches:
        match = match.strip()
        if match:
            parts = match.split("\n", 1)

            if len(parts) > 1:
                # Remove the # from the title and use it as key
                title = parts[0].replace("#", "").strip()
                # Use the rest as content
                content = parts[1].strip()
                sections[title] = content
            else:
                # Handle case where section has no content
                title = parts[0].replace("#", "").strip()
                sections[title] = None

    if len(sections) > 0:
        return sections
    else:
        return None


def extract_within(content: str, from_idx: int, to_idx: int) -> str:
    with suppress(Exception):
        extracted_content = content[from_idx:to_idx]
        parsed = json.loads(
            extracted_content
        )  # if this parses as json, set the response as that.
        if isinstance(parsed, dict):
            logging.warning(
                "The LLM did not return structured data but embedded the data into a markdown code block. This indicates the prompt is not optimised for that AI model."
            )
            content = extracted_content
    return content


def pre_format_sections(response: Any) -> Any:
    """Pre-cleaning of the response for some known, specific use cases
    prior to it being parsed for sections
    """
    if isinstance(response, dict):
        # No matter if the result is already structured, we want to go through the code below to validate the JSON
        response = json.dumps(response)

    if not isinstance(response, str):
        # if it's not a string, we make it so as it'll be parsed later
        response = str(response)

    # In some cases, the LLM will not return a structured json but instead embed the JSON into a markdown code block
    # This is not ideal and actually should not happen
    if response.startswith("```json\n") and response.endswith("\n```"):
        response = extract_within(response, 8, -3)

    if response.startswith('"{') and response.endswith('}"'):
        # Some Anthropic models embed the actual JSON dict inside a JSON string
        # In that case it gets parsed once to get rid of the first level of marshalling
        with suppress(Exception):
            response = json.loads(response)

    # Try to find any embedded code block with or without "json" label and parse it
    # This has been seen a lot in newer bedrock models
    # This is a more robust check for patterns like ```json\n{...}\n``` or ```\n{...}\n```
    matches = re.findall(r"```(?:json)?\s*\n(.*?)\n```", response, re.DOTALL)
    for block in matches:
        with suppress(Exception):
            parsed = json.loads(block)
            if isinstance(parsed, dict):
                logging.info("Extracted and parsed embedded JSON block successfully.")
                return json.dumps(parsed)

    return response


def parse_json_sections(
    response: Any,
) -> Tuple[str, Optional[Dict[str, Optional[str]]]]:
    response = pre_format_sections(response)

    with suppress(Exception):
        parsed_json = json.loads(response)

        if not isinstance(parsed_json, dict):
            return (response, None)
        sections = {}
        for key, value in parsed_json.items():
            if isinstance(value, list) and len(value) == 0:
                value = None  # For links, LLM returns '[]' which is unsightly when converted to markdown

            if isinstance(value, list):
                sections[key] = "\n\n".join(f"{str(item)}" for item in value)
            elif value is not None:
                sections[key] = str(
                    value
                )  # force to strings. We only expect markdown and don't want to give anything but a string to the UI
            else:
                sections[key] = value  # type: ignore
        if sections:
            combined = combine_sections(sections)
            return (combined, sections)  # type: ignore

    return (response, None)


def process_response_into_sections(
    response: Any,
) -> Tuple[str, Optional[Dict[str, Optional[str]]]]:
    sections = None

    if REQUEST_STRUCTURED_OUTPUT_FROM_LLM:
        (response, sections) = parse_json_sections(response)

    if not sections and PARSE_INVESTIGATION_MARKDOWN_INTO_STRUCTURED_SECTIONS:
        sections = parse_markdown_into_sections_from_hash_sign(response)
    if not sections and PARSE_INVESTIGATION_MARKDOWN_INTO_STRUCTURED_SECTIONS:
        sections = parse_markdown_into_sections_from_equal_sign(response)

    return (response, sections)


def is_response_an_incorrect_tool_call(
    sections: Optional[InputSectionsDataType], choice: dict
) -> bool:
    """Cf. https://github.com/BerriAI/litellm/issues/8241
    This code detects when LiteLLM is incapable of handling both tool calls and structured output. This only happens when the LLM is returning a single tool call.
    In that case the intention is to retry the LLM calls without structured output.
    Post processing may still try to generate a structured output from a monolithic markdown.
    """
    with suppress(Exception):
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason")
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        role = message.get("role")
        if (
            sections
            and content
            and (
                # azure
                finish_reason == "stop"
                or
                # bedrock
                finish_reason == "tool_calls"
            )
            and role == "assistant"
            and not tool_calls
        ):
            if not isinstance(content, dict):
                content = json.loads(content)
            if not isinstance(content, dict):
                return False
            for section_title in sections:
                if section_title in content:
                    return False
            return True
    return False


def clear_json_markdown(text: str):
    if text and text.startswith("```json") and text.endswith("```"):
        return text[8:-3]

    return text
