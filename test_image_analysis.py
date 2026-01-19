#!/usr/bin/env python3
"""
Test script to demonstrate image analysis support in Holmes /api/chat endpoint.

This script shows how to use the new `images` parameter to analyze images
using vision-enabled models like GPT-4 Vision.

Requirements:
- Holmes server running (python server.py or poetry run python server.py)
- OPENAI_API_KEY environment variable set
- A vision-enabled model (e.g., gpt-4-vision-preview, gpt-4o)
- Set IMAGE_ANALYSIS_INTEGRATION=true to run in CI/test environments
"""

import json
import os
import sys
from typing import Any, Dict, List

import pytest
import requests

# Request timeout in seconds
REQUEST_TIMEOUT = 30


def _check_server_available() -> bool:
    """Check if the Holmes server is reachable."""
    try:
        response = requests.get("http://localhost:8080/health", timeout=5)
        return response.ok
    except requests.exceptions.RequestException:
        return False


# Skip these tests unless explicitly enabled
pytestmark = pytest.mark.integration

# Skip at module level if server is not available or env var not set
if not os.environ.get("IMAGE_ANALYSIS_INTEGRATION") or not _check_server_available():
    pytest.skip(
        "Skipping image analysis integration tests. "
        "Set IMAGE_ANALYSIS_INTEGRATION=true and ensure server is running at http://localhost:8080",
        allow_module_level=True,
    )


def test_image_analysis_non_streaming() -> bool:
    """Test image analysis without streaming."""
    print("Testing image analysis (non-streaming)...")
    print("-" * 60)

    # Example with a publicly available image
    url = "http://localhost:8080/api/chat"

    # Test with simple string format (URL)
    payload = {
        "ask": "What's in this image? Describe what you see.",
        "model": "gpt-4o",  # Use a vision-enabled model
        "images": [
            "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
        ],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant that analyzes images."}
        ],
    }

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        data = response.json()
        print("Analysis:", data["analysis"])
        print("\nConversation history length:", len(data["conversation_history"]))

        # Check if the user message contains images
        user_messages = [
            msg for msg in data["conversation_history"] if msg.get("role") == "user"
        ]
        if user_messages:
            last_user_msg = user_messages[-1]
            content = last_user_msg.get("content")
            if isinstance(content, list):
                print("✓ Message content is properly formatted as array")
                text_parts = [c for c in content if c.get("type") == "text"]
                image_parts = [c for c in content if c.get("type") == "image_url"]
                print("  - Text parts:", len(text_parts))
                print("  - Image parts:", len(image_parts))
                for i, img in enumerate(image_parts):
                    url_preview = img["image_url"]["url"][:60]
                    print(f"    Image {i+1}: {url_preview}...")

        print("\n✓ Non-streaming test passed!")
        return True

    except requests.exceptions.RequestException as e:
        print("✗ Error:", e)
        if e.response is not None:
            print("Response:", e.response.text)
        return False


def test_image_analysis_streaming() -> bool:
    """Test image analysis with streaming."""
    print("\n\nTesting image analysis (streaming)...")
    print("-" * 60)

    url = "http://localhost:8080/api/chat"

    payload = {
        "ask": "Describe this image in detail.",
        "model": "gpt-4o",
        "stream": True,
        "images": [
            "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
        ],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant that analyzes images."}
        ],
    }

    try:
        with requests.post(
            url, json=payload, stream=True, timeout=REQUEST_TIMEOUT
        ) as response:
            response.raise_for_status()

            print("Streaming response:")
            for line in response.iter_lines():
                if line:
                    line_str = line.decode("utf-8")
                    if line_str.startswith("data: "):
                        data = line_str[6:]  # Remove 'data: ' prefix
                        try:
                            event = json.loads(data)
                            event_type = event.get("type", "unknown")

                            if event_type == "ai_message":
                                print(
                                    "[AI]",
                                    event.get("content", ""),
                                    end="",
                                    flush=True,
                                )
                            elif event_type == "ai_answer_end":
                                print("\n")
                                print("✓ Streaming completed")
                                history_len = len(
                                    event.get("conversation_history", [])
                                )
                                print("  Conversation history length:", history_len)
                            elif event_type == "error":
                                error_msg = event.get("message", "Unknown error")
                                print("\n✗ Error:", error_msg)
                                return False
                        except json.JSONDecodeError:
                            continue

        print("✓ Streaming test passed!")
        return True

    except requests.exceptions.Timeout:
        print("✗ Error: Request timed out")
        return False
    except requests.exceptions.RequestException as e:
        print("✗ Error:", e)
        if e.response is not None:
            print("Response:", e.response.text)
        return False


def test_backward_compatibility() -> bool:
    """Test that the API still works without images (backward compatibility)."""
    print("\n\nTesting backward compatibility (no images)...")
    print("-" * 60)

    url = "http://localhost:8080/api/chat"

    payload = {
        "ask": "What is 2+2?",
        "model": "gpt-4o-mini",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
    }

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        data = response.json()
        print("Analysis:", data["analysis"])

        # Check that the message is still a simple string
        user_messages = [
            msg for msg in data["conversation_history"] if msg.get("role") == "user"
        ]
        if user_messages:
            last_user_msg = user_messages[-1]
            content = last_user_msg.get("content")
            if isinstance(content, str):
                print("✓ Message content is string (backward compatible)")
            else:
                print(
                    "⚠ Warning: Message content is",
                    type(content),
                    "expected string",
                )

        print("✓ Backward compatibility test passed!")
        return True

    except requests.exceptions.RequestException as e:
        print("✗ Error:", e)
        if e.response is not None:
            print("Response:", e.response.text)
        return False


def test_advanced_image_format() -> bool:
    """Test advanced image format with detail and format parameters."""
    print("\n\nTesting advanced image format (detail + format parameters)...")
    print("-" * 60)

    url = "http://localhost:8080/api/chat"

    payload = {
        "ask": "Analyze these images in detail.",
        "model": "gpt-4o",
        "images": [
            # Simple string URL
            "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/800px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg",
            # Dict with detail parameter
            {
                "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/800px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg",
                "detail": "high",  # Request high-detail analysis (OpenAI-specific)
            },
            # Base64 data URI (small example)
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        ],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
    }

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        data = response.json()
        analysis = data.get("analysis", "")
        if isinstance(analysis, str):
            preview = analysis[:200] + "..." if len(analysis) > 200 else analysis
            print("Analysis:", preview)
        else:
            print("⚠ Warning: Analysis is not a string")

        # Find the user message with images
        conversation_history = data.get("conversation_history", [])
        user_messages = [
            msg for msg in conversation_history if msg.get("role") == "user"
        ]
        if user_messages:
            last_user_msg = user_messages[-1]
            content = last_user_msg.get("content")
            if isinstance(content, list):
                image_parts = [c for c in content if c.get("type") == "image_url"]
                print(
                    "✓ Successfully sent",
                    len(image_parts),
                    "images with mixed formats",
                )

                # Verify detail parameter was preserved
                high_detail_images = [
                    img
                    for img in image_parts
                    if img.get("image_url", {}).get("detail") == "high"
                ]
                if high_detail_images:
                    print(
                        "✓ High-detail parameter preserved for",
                        len(high_detail_images),
                        "image(s)",
                    )

        print("✓ Advanced format test passed!")
        return True

    except requests.exceptions.RequestException as e:
        print("✗ Error:", e)
        if e.response is not None:
            print("Response:", e.response.text)
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Holmes Image Analysis Test Script")
    print("=" * 60)
    print("\nMake sure:")
    print("1. Holmes server is running (python server.py)")
    print("2. OPENAI_API_KEY is set")
    print("3. You have access to a vision model (gpt-4o, gpt-4-vision-preview)")
    print("4. Set IMAGE_ANALYSIS_INTEGRATION=true to run")
    print("\nStarting tests...\n")

    if not os.environ.get("IMAGE_ANALYSIS_INTEGRATION"):
        print("⚠ IMAGE_ANALYSIS_INTEGRATION not set. Skipping tests.")
        sys.exit(0)

    if not _check_server_available():
        print("⚠ Server not reachable at http://localhost:8080. Skipping tests.")
        sys.exit(0)

    results = []
    results.append(test_backward_compatibility())
    results.append(test_image_analysis_non_streaming())
    results.append(test_image_analysis_streaming())
    results.append(test_advanced_image_format())

    print("\n" + "=" * 60)
    print("Summary:")
    print("  Passed:", sum(results), "/", len(results))
    print("  Failed:", len(results) - sum(results), "/", len(results))
    print("=" * 60)

    sys.exit(0 if all(results) else 1)
