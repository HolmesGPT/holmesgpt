#!/usr/bin/env python3
"""
Test script to demonstrate image analysis support in Holmes /api/chat endpoint.

This script shows how to use the new `images` parameter to analyze images
using vision-enabled models like GPT-4 Vision.

Requirements:
- Holmes server running (python server.py or poetry run python server.py)
- OPENAI_API_KEY environment variable set
- A vision-enabled model (e.g., gpt-4-vision-preview, gpt-4o)
"""

import requests
import json
import sys


def test_image_analysis_non_streaming():
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
        ]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        print(f"Analysis: {data['analysis']}")
        print(f"\nConversation history length: {len(data['conversation_history'])}")

        # Check if the user message contains images
        user_messages = [msg for msg in data['conversation_history'] if msg.get('role') == 'user']
        if user_messages:
            last_user_msg = user_messages[-1]
            content = last_user_msg.get('content')
            if isinstance(content, list):
                print(f"✓ Message content is properly formatted as array")
                text_parts = [c for c in content if c.get('type') == 'text']
                image_parts = [c for c in content if c.get('type') == 'image_url']
                print(f"  - Text parts: {len(text_parts)}")
                print(f"  - Image parts: {len(image_parts)}")
                for i, img in enumerate(image_parts):
                    print(f"    Image {i+1}: {img['image_url']['url'][:60]}...")

        print("\n✓ Non-streaming test passed!")
        return True

    except requests.exceptions.RequestException as e:
        print(f"✗ Error: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return False


def test_image_analysis_streaming():
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
        ]
    }

    try:
        response = requests.post(url, json=payload, stream=True)
        response.raise_for_status()

        print("Streaming response:")
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: '):
                    data = line_str[6:]  # Remove 'data: ' prefix
                    try:
                        event = json.loads(data)
                        event_type = event.get('type', 'unknown')

                        if event_type == 'ai_message':
                            print(f"[AI] {event.get('content', '')}", end='', flush=True)
                        elif event_type == 'ai_answer_end':
                            print("\n")
                            print(f"✓ Streaming completed")
                            print(f"  Conversation history length: {len(event.get('conversation_history', []))}")
                        elif event_type == 'error':
                            print(f"\n✗ Error: {event.get('message', 'Unknown error')}")
                            return False
                    except json.JSONDecodeError:
                        continue

        print("✓ Streaming test passed!")
        return True

    except requests.exceptions.RequestException as e:
        print(f"✗ Error: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return False


def test_backward_compatibility():
    """Test that the API still works without images (backward compatibility)."""
    print("\n\nTesting backward compatibility (no images)...")
    print("-" * 60)

    url = "http://localhost:8080/api/chat"

    payload = {
        "ask": "What is 2+2?",
        "model": "gpt-4o-mini",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        print(f"Analysis: {data['analysis']}")

        # Check that the message is still a simple string
        user_messages = [msg for msg in data['conversation_history'] if msg.get('role') == 'user']
        if user_messages:
            last_user_msg = user_messages[-1]
            content = last_user_msg.get('content')
            if isinstance(content, str):
                print(f"✓ Message content is string (backward compatible)")
            else:
                print(f"⚠ Warning: Message content is {type(content)}, expected string")

        print("✓ Backward compatibility test passed!")
        return True

    except requests.exceptions.RequestException as e:
        print(f"✗ Error: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return False


def test_advanced_image_format():
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
                "detail": "high"  # Request high-detail analysis (OpenAI-specific)
            },
            # Base64 data URI (small example)
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        ],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        print(f"Analysis: {data['analysis'][:200]}...")

        # Find the user message with images
        user_messages = [msg for msg in data['conversation_history'] if msg.get('role') == 'user']
        if user_messages:
            last_user_msg = user_messages[-1]
            content = last_user_msg.get('content')
            if isinstance(content, list):
                image_parts = [c for c in content if c.get('type') == 'image_url']
                print(f"✓ Successfully sent {len(image_parts)} images with mixed formats")

                # Verify detail parameter was preserved
                high_detail_images = [
                    img for img in image_parts
                    if img.get('image_url', {}).get('detail') == 'high'
                ]
                if high_detail_images:
                    print(f"✓ High-detail parameter preserved for {len(high_detail_images)} image(s)")

        print("✓ Advanced format test passed!")
        return True

    except requests.exceptions.RequestException as e:
        print(f"✗ Error: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Holmes Image Analysis Test Script")
    print("=" * 60)
    print("\nMake sure:")
    print("1. Holmes server is running (python server.py)")
    print("2. OPENAI_API_KEY is set")
    print("3. You have access to a vision model (gpt-4o, gpt-4-vision-preview)")
    print("\nStarting tests...\n")

    results = []
    results.append(test_backward_compatibility())
    results.append(test_image_analysis_non_streaming())
    results.append(test_image_analysis_streaming())
    results.append(test_advanced_image_format())

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Passed: {sum(results)}/{len(results)}")
    print(f"  Failed: {len(results) - sum(results)}/{len(results)}")
    print("=" * 60)

    sys.exit(0 if all(results) else 1)
