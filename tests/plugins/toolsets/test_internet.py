import os
import re
from pathlib import Path
from typing import List

import pytest
from pydantic import BaseModel

from holmes.core.tools import ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.internet.internet import InternetToolset, html_to_markdown
from tests.conftest import create_mock_tool_invoke_context

THIS_DIR = os.path.dirname(__file__)
FIXTURES_DIR = os.path.join(THIS_DIR, "fixtures", "test_internet")


TEST_URL = "https://www.example.com"
EXPECTED_TEST_RESULT = """
Example Domain

Example Domain
==============

This domain is for use in illustrative examples in documents. You may use this
domain in literature without prior coordination or asking for permission.

[More information...](https://www.iana.org/domains/example)
""".strip()


def read_file(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read().strip()


def write_file(file_path: Path, content: str):
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)


def parse_fixture_id(file_name: str) -> str:
    match = re.match(r"fixture(\d+)", file_name)
    if match:
        # Extract the number
        return match.group(1)
    else:
        raise Exception(f"Could not find fixture id in filename {file_name}")


def clean_content(text):
    lines = text.splitlines()
    cleaned_lines = [line.rstrip() for line in lines]
    return "\n".join(cleaned_lines).strip()


class Fixture(BaseModel):
    id: str
    input: str
    expected_output: str
    input_file_path: Path
    expected_output_file_path: Path


def load_all_fixtures() -> List[Fixture]:
    """
    Load the fixtures in the fixtures folder by pair fixtureX_input.html with fixtureX_output.md
    to feed the test
    """
    fixtures_dir = Path(FIXTURES_DIR)
    input_files = sorted([f for f in fixtures_dir.glob("fixture*.html")])
    test_cases = []

    for input_file in input_files:
        number = parse_fixture_id(input_file.stem)
        output_file = fixtures_dir / f"fixture{number}_output.md"

        if output_file.exists():
            input_content = read_file(input_file)
            output_content = read_file(output_file)
            test_cases.append(
                Fixture(
                    id=number,
                    input=input_content,
                    expected_output=clean_content(output_content),
                    input_file_path=input_file,
                    expected_output_file_path=output_file,
                )
            )

    assert len(test_cases) > 0
    return test_cases


def idfn(val):
    if isinstance(val, Fixture):
        return f"Fixture #{val.id}"
    else:
        return str(val)


@pytest.mark.parametrize("fixture", load_all_fixtures(), ids=idfn)
def test_html_to_markdown(fixture: Fixture):
    actual_output = html_to_markdown(fixture.input)
    print(actual_output)
    actual_output = clean_content(actual_output)
    match = actual_output == fixture.expected_output
    actual_file_path_for_debugging = fixture.expected_output_file_path.with_suffix(
        ".actual"
    )
    if not match:
        write_file(actual_file_path_for_debugging, actual_output)
    assert match, f"Values mismatch. Run the following command to compare expected with actual: `diff {fixture.expected_output_file_path} {actual_file_path_for_debugging}`"


def test_fetch_webpage(responses):
    responses.get(
        TEST_URL,
        status=200,
        body=EXPECTED_TEST_RESULT,
    )
    toolset = InternetToolset()
    toolset.status = ToolsetStatusEnum.ENABLED
    tool_executor = ToolExecutor(toolsets=[toolset])
    fetch_webpage_tool = tool_executor.get_tool_by_name("fetch_webpage")
    assert fetch_webpage_tool
    context = create_mock_tool_invoke_context()
    actual_output = fetch_webpage_tool.invoke({"url": TEST_URL}, context)
    print(actual_output.data)
    assert actual_output.data
    assert actual_output.data.strip() == EXPECTED_TEST_RESULT
