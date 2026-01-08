# CLI Image Analysis Support - Implementation Guide

This document provides a comprehensive guide for adding image analysis support to the Holmes CLI `ask` command.

## Overview

Add the ability to attach images when asking questions via the CLI, similar to how files are currently attached with `--file` / `-f`. Images will be converted to base64 and sent to the `/api/chat` endpoint (when using server mode) or passed directly to the LLM (when using local mode).

## Current State

The HTTP server `/api/chat` endpoint **already supports images** in multiple formats:
- Simple string URLs: `["https://example.com/image.jpg"]`
- Base64 data URIs: `["data:image/jpeg;base64,/9j/..."]`
- Advanced dict format with parameters:
  ```python
  [{
      "url": "https://example.com/image.jpg",
      "detail": "high",  # OpenAI-specific: low/high/auto
      "format": "image/jpeg"  # MIME type for providers that need it
  }]
  ```

## Implementation Tasks

### 1. Add CLI Option for Images

**File:** `holmes/main.py`

**Location:** In the `ask()` function parameters (around line 189, after `include_file`)

**Code to add:**
```python
include_image: Optional[List[Path]] = typer.Option(
    [],
    "--image",
    "-img",
    help="Image file to analyze (jpg, png, gif, webp). Can specify multiple times to add multiple images",
),
```

**Why:** Follows the same pattern as `--file` / `-f` for consistency

### 2. Create Image Processing Utility

**File:** `holmes/utils/image_utils.py` (NEW FILE)

**Purpose:** Convert local image files to base64 data URIs

**Implementation:**
```python
"""Utilities for processing images for vision models."""
import base64
from pathlib import Path
from typing import List, Optional

# Supported image formats
SUPPORTED_IMAGE_FORMATS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def get_image_mime_type(file_path: Path) -> Optional[str]:
    """Get MIME type for an image file based on extension.

    Args:
        file_path: Path to image file

    Returns:
        MIME type string or None if unsupported
    """
    suffix = file_path.suffix.lower()
    return SUPPORTED_IMAGE_FORMATS.get(suffix)


def validate_image_file(file_path: Path) -> None:
    """Validate that a file is a supported image format.

    Args:
        file_path: Path to image file

    Raises:
        ValueError: If file doesn't exist or has unsupported format
    """
    if not file_path.exists():
        raise ValueError(f"Image file not found: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    mime_type = get_image_mime_type(file_path)
    if mime_type is None:
        supported = ", ".join(SUPPORTED_IMAGE_FORMATS.keys())
        raise ValueError(
            f"Unsupported image format: {file_path.suffix}. "
            f"Supported formats: {supported}"
        )


def image_to_base64_data_uri(file_path: Path) -> str:
    """Convert an image file to a base64 data URI.

    Args:
        file_path: Path to image file

    Returns:
        Base64 data URI string (e.g., "data:image/jpeg;base64,/9j/...")

    Raises:
        ValueError: If file is invalid or unsupported format
    """
    validate_image_file(file_path)

    mime_type = get_image_mime_type(file_path)
    with open(file_path, "rb") as f:
        image_data = f.read()

    base64_data = base64.b64encode(image_data).decode("utf-8")
    return f"data:{mime_type};base64,{base64_data}"


def process_image_paths(
    image_paths: Optional[List[Path]],
    console=None
) -> List[str]:
    """Process a list of image paths into base64 data URIs.

    Args:
        image_paths: List of paths to image files
        console: Optional Rich console for progress output

    Returns:
        List of base64 data URI strings

    Raises:
        ValueError: If any image is invalid
    """
    if not image_paths:
        return []

    data_uris = []
    for image_path in image_paths:
        try:
            if console:
                console.print(f"[dim]Loading image: {image_path}[/dim]")
            data_uri = image_to_base64_data_uri(image_path)
            data_uris.append(data_uri)
        except ValueError as e:
            if console:
                console.print(f"[bold red]Error loading image:[/bold red] {e}")
            raise

    return data_uris
```

**Why:**
- Centralized image processing logic
- Validation and error handling
- Easy to test and maintain
- Supports all common image formats

### 3. Update `build_initial_ask_messages` Function

**File:** `holmes/core/prompt.py`

**Function:** `build_initial_ask_messages()` (around line 74)

**Changes needed:**

**a) Add `image_paths` parameter:**
```python
def build_initial_ask_messages(
    console: Console,
    initial_user_prompt: str,
    file_paths: Optional[List[Path]],
    tool_executor: Any,  # ToolExecutor type
    runbooks: Union[RunbookCatalog, Dict, None] = None,
    system_prompt_additions: Optional[str] = None,
    image_paths: Optional[List[Path]] = None,  # NEW PARAMETER
) -> List[Dict]:
```

**b) Process images and update message structure:**
```python
from holmes.utils.image_utils import process_image_paths

def build_initial_ask_messages(
    console: Console,
    initial_user_prompt: str,
    file_paths: Optional[List[Path]],
    tool_executor: Any,
    runbooks: Union[RunbookCatalog, Dict, None] = None,
    system_prompt_additions: Optional[str] = None,
    image_paths: Optional[List[Path]] = None,
) -> List[Dict]:
    """Build the initial messages for the AI call.

    Args:
        console: Rich console for output
        initial_user_prompt: The user's prompt
        file_paths: Optional list of files to include
        tool_executor: The tool executor with available toolsets
        runbooks: Optional runbook catalog
        system_prompt_additions: Optional additional system prompt content
        image_paths: Optional list of image files to analyze
    """
    # ... existing system prompt code ...

    # Append files to user prompt
    user_prompt_with_files = append_all_files_to_user_prompt(
        console, initial_user_prompt, file_paths
    )

    user_prompt_with_files += get_tasks_management_system_reminder()

    runbooks_ctx = generate_runbooks_args(
        runbook_catalog=runbooks,
    )
    user_prompt_with_files = generate_user_prompt(
        user_prompt_with_files,
        runbooks_ctx,
    )

    # Process images if provided
    if image_paths:
        image_data_uris = process_image_paths(image_paths, console)

        # Build content array with text and images
        content = [{"type": "text", "text": user_prompt_with_files}]
        for data_uri in image_data_uris:
            content.append({
                "type": "image_url",
                "image_url": {"url": data_uri}
            })

        messages = [
            {"role": "system", "content": system_prompt_rendered},
            {"role": "user", "content": content},  # Array format for vision
        ]
    else:
        # Standard text-only format
        messages = [
            {"role": "system", "content": system_prompt_rendered},
            {"role": "user", "content": user_prompt_with_files},
        ]

    return messages
```

**Why:**
- Follows existing pattern (similar to file handling)
- Uses array content format when images present
- Maintains backward compatibility (no images = string content)

### 4. Update `ask()` Command to Pass Images

**File:** `holmes/main.py`

**Function:** `ask()` command (around line 302)

**Change:**
```python
# Before:
messages = build_initial_ask_messages(
    console,
    prompt,  # type: ignore
    include_file,
    ai.tool_executor,
    config.get_runbook_catalog(),
    system_prompt_additions,
)

# After:
messages = build_initial_ask_messages(
    console,
    prompt,  # type: ignore
    include_file,
    ai.tool_executor,
    config.get_runbook_catalog(),
    system_prompt_additions,
    image_paths=include_image,  # NEW PARAMETER
)
```

### 5. Update Interactive Mode

**File:** `holmes/interactive.py`

**Function:** `run_interactive_loop()` (around line 989)

**Changes needed:**

**a) Add `include_images` parameter:**
```python
def run_interactive_loop(
    ai: ToolCallingLLM,
    console: Console,
    initial_user_input: Optional[str],
    include_files: Optional[List[Path]],
    show_tool_output: bool,
    tracer=None,
    runbooks=None,
    system_prompt_additions: Optional[str] = None,
    check_version: bool = True,
    feedback_callback: Optional[FeedbackCallback] = None,
    json_output_file: Optional[str] = None,
    include_images: Optional[List[Path]] = None,  # NEW PARAMETER
) -> None:
```

**b) Pass images to initial message building:**
```python
# Find the code that builds initial messages (around line 1050-1100)
# Update it to include image_paths parameter:

messages = build_initial_ask_messages(
    console,
    initial_user_input,
    include_files,
    ai.tool_executor,
    runbooks,
    system_prompt_additions,
    image_paths=include_images,  # NEW PARAMETER
)
```

**c) Add slash command for attaching images:**

Find the slash command handler section (look for `/help`, `/exit` commands) and add:

```python
# In the command parsing section (around line 1150-1200)
elif user_input.startswith("/attach-image "):
    image_path_str = user_input[14:].strip()  # Remove "/attach-image "
    image_path = Path(image_path_str)

    try:
        from holmes.utils.image_utils import validate_image_file
        validate_image_file(image_path)

        # Add to images list (initialize if needed)
        if not hasattr(run_interactive_loop, '_attached_images'):
            run_interactive_loop._attached_images = []
        run_interactive_loop._attached_images.append(image_path)

        console.print(f"[green]✓ Attached image:[/green] {image_path}")
        console.print(f"[dim]Total images: {len(run_interactive_loop._attached_images)}[/dim]")
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
    continue

elif user_input == "/clear-images":
    if hasattr(run_interactive_loop, '_attached_images'):
        count = len(run_interactive_loop._attached_images)
        run_interactive_loop._attached_images = []
        console.print(f"[green]✓ Cleared {count} attached image(s)[/green]")
    else:
        console.print("[dim]No images attached[/dim]")
    continue
```

**d) Update help text:**
```python
# In the /help command output (around line 1120)
Add:
    /attach-image <path>  - Attach an image for analysis
    /clear-images         - Clear all attached images
```

**e) Include attached images when sending messages:**
```python
# When building messages for each user input in the loop:
attached_images = getattr(run_interactive_loop, '_attached_images', [])

# Build message with images if present
if attached_images:
    from holmes.utils.image_utils import process_image_paths
    image_data_uris = process_image_paths(attached_images, console)

    content = [{"type": "text", "text": user_input}]
    for data_uri in image_data_uris:
        content.append({
            "type": "image_url",
            "image_url": {"url": data_uri}
        })

    messages.append({"role": "user", "content": content})

    # Clear images after use (or keep for multiple turns - user's choice)
    # run_interactive_loop._attached_images = []
else:
    messages.append({"role": "user", "content": user_input})
```

**Why:**
- Interactive mode gets same image capabilities as non-interactive
- Slash commands provide convenient way to attach images mid-conversation
- Clear command allows managing attached images

### 6. Update `main.py` to Pass Images to Interactive Mode

**File:** `holmes/main.py`

**Location:** Around line 289 in `ask()` function

**Change:**
```python
# Before:
if interactive:
    run_interactive_loop(
        ai,
        console,
        prompt,
        include_file,
        show_tool_output,
        tracer,
        config.get_runbook_catalog(),
        system_prompt_additions,
        json_output_file=json_output_file,
    )
    return

# After:
if interactive:
    run_interactive_loop(
        ai,
        console,
        prompt,
        include_file,
        show_tool_output,
        tracer,
        config.get_runbook_catalog(),
        system_prompt_additions,
        json_output_file=json_output_file,
        include_images=include_image,  # NEW PARAMETER
    )
    return
```

### 7. Add Tests

**File:** `tests/test_image_utils.py` (NEW FILE)

```python
"""Tests for image utilities."""
import base64
from pathlib import Path
import pytest

from holmes.utils.image_utils import (
    get_image_mime_type,
    validate_image_file,
    image_to_base64_data_uri,
    process_image_paths,
)


def test_get_image_mime_type():
    """Test MIME type detection."""
    assert get_image_mime_type(Path("test.jpg")) == "image/jpeg"
    assert get_image_mime_type(Path("test.jpeg")) == "image/jpeg"
    assert get_image_mime_type(Path("test.png")) == "image/png"
    assert get_image_mime_type(Path("test.gif")) == "image/gif"
    assert get_image_mime_type(Path("test.webp")) == "image/webp"
    assert get_image_mime_type(Path("test.txt")) is None


def test_validate_image_file_missing():
    """Test validation fails for missing file."""
    with pytest.raises(ValueError, match="not found"):
        validate_image_file(Path("/nonexistent/image.jpg"))


def test_validate_image_file_unsupported(tmp_path):
    """Test validation fails for unsupported format."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("not an image")

    with pytest.raises(ValueError, match="Unsupported image format"):
        validate_image_file(test_file)


def test_image_to_base64_data_uri(tmp_path):
    """Test converting image to base64 data URI."""
    # Create a tiny valid PNG (1x1 transparent pixel)
    png_data = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    test_file = tmp_path / "test.png"
    test_file.write_bytes(png_data)

    result = image_to_base64_data_uri(test_file)

    assert result.startswith("data:image/png;base64,")
    assert len(result) > 50  # Has actual base64 data


def test_process_image_paths(tmp_path):
    """Test processing multiple images."""
    # Create two test images
    png_data = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    img1 = tmp_path / "test1.png"
    img1.write_bytes(png_data)

    img2 = tmp_path / "test2.png"
    img2.write_bytes(png_data)

    results = process_image_paths([img1, img2])

    assert len(results) == 2
    assert all(r.startswith("data:image/png;base64,") for r in results)


def test_process_image_paths_empty():
    """Test processing empty list."""
    assert process_image_paths([]) == []
    assert process_image_paths(None) == []
```

**File:** `tests/test_ask_with_images.py` (NEW FILE)

```python
"""Integration tests for ask command with images."""
import base64
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from holmes.core.prompt import build_initial_ask_messages


@pytest.fixture
def mock_tool_executor():
    """Mock tool executor."""
    executor = MagicMock()
    executor.toolsets = []
    return executor


@pytest.fixture
def test_image(tmp_path):
    """Create a test image."""
    png_data = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    image_file = tmp_path / "test.png"
    image_file.write_bytes(png_data)
    return image_file


def test_build_initial_ask_messages_with_images(mock_tool_executor, test_image):
    """Test building messages with images."""
    from unittest.mock import MagicMock

    console = MagicMock()

    messages = build_initial_ask_messages(
        console=console,
        initial_user_prompt="What's in this image?",
        file_paths=None,
        tool_executor=mock_tool_executor,
        image_paths=[test_image],
    )

    assert len(messages) == 2  # system + user
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"

    # User message should have array content with text + image
    content = messages[1]["content"]
    assert isinstance(content, list)
    assert len(content) == 2  # text + 1 image

    # Verify text
    assert content[0]["type"] == "text"
    assert "What's in this image?" in content[0]["text"]

    # Verify image
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_initial_ask_messages_without_images(mock_tool_executor):
    """Test building messages without images (backward compatibility)."""
    from unittest.mock import MagicMock

    console = MagicMock()

    messages = build_initial_ask_messages(
        console=console,
        initial_user_prompt="Hello",
        file_paths=None,
        tool_executor=mock_tool_executor,
    )

    assert len(messages) == 2
    assert messages[1]["role"] == "user"

    # Without images, content should be a string
    content = messages[1]["content"]
    assert isinstance(content, str)
    assert "Hello" in content
```

## Usage Examples

### Non-interactive Mode

```bash
# Single image
holmes ask "What's in this screenshot?" --image screenshot.png

# Multiple images
holmes ask "Compare these dashboards" --image dash1.png --image dash2.png

# Mix with files
holmes ask "Analyze this error" --file error.log --image screenshot.png

# From piped input
kubectl get pods | holmes ask "What's wrong with these pods?" --image cluster-overview.png
```

### Interactive Mode

```bash
# Start with initial images
holmes ask "Let's analyze my system" --image system-metrics.png --interactive

# In interactive mode, attach images:
> /attach-image screenshot.png
✓ Attached image: screenshot.png
> What's in this image?

> /attach-image another.png
✓ Attached image: another.png
Total images: 2
> Compare these two images

> /clear-images
✓ Cleared 2 attached image(s)
```

## Error Handling

Add proper error messages for:
- File not found: `"Image file not found: {path}"`
- Unsupported format: `"Unsupported image format: {ext}. Supported: jpg, jpeg, png, gif, webp"`
- File too large (optional): `"Image too large: {size}MB. Maximum: 10MB"`

## Model Compatibility

The CLI should work with any vision-enabled model:
- OpenAI: `gpt-4o`, `gpt-4-vision-preview`, `gpt-4o-mini`
- Anthropic: `claude-3.5-sonnet`, `claude-3-opus`, `claude-3-haiku`
- Google: `gemini-1.5-pro`, `gemini-1.5-flash`

Example:
```bash
holmes ask "Describe this image" --image screenshot.png --model gpt-4o
holmes ask "Analyze this diagram" --image diagram.png --model claude-3.5-sonnet
```

## Implementation Checklist

- [ ] Add `--image` / `-img` option to `ask()` command
- [ ] Create `holmes/utils/image_utils.py` with image processing functions
- [ ] Update `build_initial_ask_messages()` to accept `image_paths` parameter
- [ ] Update `build_initial_ask_messages()` to build array content when images present
- [ ] Update `ask()` command to pass `include_image` to `build_initial_ask_messages()`
- [ ] Add `include_images` parameter to `run_interactive_loop()`
- [ ] Add `/attach-image` and `/clear-images` slash commands
- [ ] Update interactive mode to handle attached images
- [ ] Pass `include_image` from `ask()` to `run_interactive_loop()`
- [ ] Add tests for `image_utils.py`
- [ ] Add integration tests for messages with images
- [ ] Update CLI help text / documentation
- [ ] Test with different image formats (jpg, png, gif, webp)
- [ ] Test with multiple models (OpenAI, Anthropic, etc.)

## Notes

- Images are converted to base64 before sending to LLM
- Base64 encoding increases size by ~33%, so large images may hit token limits
- Consider adding file size validation (e.g., warn if image > 5MB)
- LiteLLM handles provider-specific conversions automatically
- The HTTP endpoint already supports all this - CLI just needs to format images correctly

## Architecture Decision

**Why base64 encoding in CLI?**
- Simple: No need for temporary file servers or URL hosting
- Secure: Images don't need to be publicly accessible
- Consistent: Same as how the HTTP API handles local images
- Works offline: No internet required beyond LLM API calls

**Why not URL-based?**
- URLs require images to be publicly accessible (security issue)
- Would need additional infrastructure (file server, S3, etc.)
- Base64 is the standard approach for local files in vision APIs
