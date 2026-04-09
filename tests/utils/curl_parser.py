"""
Parser for extracting and converting curl commands from markdown documentation.

This module enables documentation-driven testing: curl examples in docs serve as
both documentation AND test cases, eliminating duplication.

Usage in docs (test metadata is invisible in rendered markdown):
```bash
<!-- test: status=200, has_fields=analysis,tool_calls -->
curl -X POST http://<HOLMES-URL>/api/chat \
  -H "Content-Type: application/json" \
  -d '{"ask": "test question"}'
```
"""

import copy
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class CurlCommand:
    """Parsed curl command with test metadata."""

    method: str = "GET"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    data: Optional[str] = None
    json_data: Optional[dict[str, Any]] = None
    flags: list[str] = field(default_factory=list)

    # Test metadata (from markdown comments)
    expected_status: int = 200
    expected_fields: list[str] = field(default_factory=list)
    skip: bool = False
    description: str = ""
    test_id: str = ""

    # Source info
    source_file: str = ""
    line_number: int = 0


@dataclass
class DocCurlTest:
    """A testable curl example from documentation."""

    curl: CurlCommand
    raw_command: str
    test_metadata: dict[str, Any]


def parse_curl_command(curl_str: str) -> CurlCommand:
    """
    Parse a curl command string into a CurlCommand object.

    Handles:
    - Multi-line commands with backslash continuations
    - Various curl flags (-X, -H, -d, -s, -N, etc.)
    - JSON data parsing
    - URL placeholder substitution
    """
    # Normalize multi-line commands - handle various backslash patterns
    # Pattern 1: backslash at end of line (standard shell continuation)
    curl_str = curl_str.replace("\\\n", " ")
    # Pattern 2: backslash followed by whitespace then newline (from markdown)
    curl_str = re.sub(r"\\\s*\n", " ", curl_str)
    # Pattern 3: standalone backslash between tokens (from markdown rendering)
    curl_str = re.sub(r"\s+\\\s+", " ", curl_str)
    # Normalize whitespace
    curl_str = " ".join(curl_str.split())

    cmd = CurlCommand()

    try:
        parts = shlex.split(curl_str)
    except ValueError:
        # Handle malformed quotes
        parts = curl_str.split()

    i = 0
    while i < len(parts):
        part = parts[i]

        if part == "curl":
            i += 1
            continue

        if part == "-X" and i + 1 < len(parts):
            cmd.method = parts[i + 1]
            i += 2
            continue

        if part == "-H" and i + 1 < len(parts):
            header = parts[i + 1]
            if ":" in header:
                key, val = header.split(":", 1)
                cmd.headers[key.strip()] = val.strip()
            i += 2
            continue

        if part == "-d" and i + 1 < len(parts):
            cmd.data = parts[i + 1]
            # Try to parse as JSON
            try:
                cmd.json_data = json.loads(cmd.data)
            except json.JSONDecodeError:
                pass
            i += 2
            continue

        if part in ("-s", "-N", "-i", "-v", "--silent", "--no-buffer"):
            cmd.flags.append(part)
            i += 1
            continue

        if part.startswith("-"):
            # Unknown flag with potential value
            if i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue

        # Assume it's the URL
        if not cmd.url and (part.startswith("http") or part.startswith("<")):
            cmd.url = part
            i += 1
            continue

        i += 1

    return cmd


def parse_test_metadata(comment: str) -> dict[str, Any]:
    """
    Parse test metadata from a markdown comment.

    Format: <!-- test: key1=value1, key2=value2 -->

    Supported keys:
    - status: expected HTTP status code (default: 200)
    - has_fields: comma-separated list of expected response fields
    - skip: skip this test (true/false)
    - id: test identifier
    - desc: test description
    """
    metadata: dict[str, Any] = {
        "status": 200,
        "has_fields": [],
        "skip": False,
        "id": "",
        "desc": "",
    }

    # Extract key=value pairs
    match = re.search(r"<!--\s*test:\s*(.+?)\s*-->", comment, re.IGNORECASE)
    if not match:
        return metadata

    pairs_str = match.group(1)

    # Parse each key=value pair
    for pair in pairs_str.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue

        key, value = pair.split("=", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "status":
            try:
                metadata["status"] = int(value)
            except ValueError:
                pass
        elif key == "has_fields":
            metadata["has_fields"] = [f.strip() for f in value.split("|")]
        elif key == "skip":
            metadata["skip"] = value.lower() in ("true", "1", "yes")
        elif key == "id":
            metadata["id"] = value
        elif key == "desc":
            metadata["desc"] = value

    return metadata


def extract_curl_tests_from_markdown(
    content: str, source_file: str = ""
) -> list[DocCurlTest]:
    """
    Extract all testable curl commands from markdown content.

    Looks for bash code blocks containing curl commands, optionally
    preceded by test metadata comments.
    """
    tests = []

    # Pattern to find bash code blocks with optional preceding test comment
    # The test comment can be inside or outside the code block
    pattern = re.compile(
        r"(?:<!--\s*test:\s*[^>]+-->\s*)?"  # Optional test comment before block
        r"```bash\n"
        r"((?:<!--\s*test:\s*[^>]+-->\s*)?"  # Optional test comment inside block
        r".*?curl\s+.*?)"  # Curl command
        r"\n```",
        re.DOTALL,
    )

    for match in pattern.finditer(content):
        block_content = match.group(1)

        # Check for test metadata comment (inside or before block)
        full_match = match.group(0)
        test_meta_match = re.search(r"<!--\s*test:\s*[^>]+-->", full_match)

        if test_meta_match:
            metadata = parse_test_metadata(test_meta_match.group(0))
        else:
            # No test annotation - skip this curl (it's just documentation)
            continue

        # Extract just the curl command (remove any comments)
        curl_lines = []
        for line in block_content.split("\n"):
            line = line.strip()
            if line.startswith("#") or line.startswith("<!--"):
                continue
            if line:
                curl_lines.append(line)

        curl_str = " ".join(curl_lines)
        if not curl_str.startswith("curl"):
            # Find curl in the line
            curl_idx = curl_str.find("curl")
            if curl_idx >= 0:
                curl_str = curl_str[curl_idx:]

        cmd = parse_curl_command(curl_str)
        cmd.source_file = source_file
        cmd.expected_status = metadata.get("status", 200)
        cmd.expected_fields = metadata.get("has_fields", [])
        cmd.skip = metadata.get("skip", False)
        cmd.test_id = metadata.get("id", "")
        cmd.description = metadata.get("desc", "")

        # Calculate approximate line number
        cmd.line_number = content[: match.start()].count("\n") + 1

        tests.append(
            DocCurlTest(curl=cmd, raw_command=curl_str, test_metadata=metadata)
        )

    return tests


def extract_curl_tests_from_file(filepath: Path) -> list[DocCurlTest]:
    """Extract testable curl commands from a markdown file."""
    content = filepath.read_text()
    return extract_curl_tests_from_markdown(content, str(filepath))


def substitute_placeholders(
    curl: CurlCommand, replacements: dict[str, str]
) -> CurlCommand:
    """
    Substitute placeholders in a curl command.

    Common placeholders:
    - <HOLMES-URL> -> localhost:8080
    - <your-api-key> -> test-api-key
    """
    result = copy.deepcopy(curl)

    # Substitute in URL
    for placeholder, value in replacements.items():
        result.url = result.url.replace(placeholder, value)

    # Substitute in data
    if result.data:
        for placeholder, value in replacements.items():
            result.data = result.data.replace(placeholder, value)
        # Re-parse JSON if data was modified
        try:
            result.json_data = json.loads(result.data)
        except json.JSONDecodeError:
            pass

    return result
