#!/usr/bin/env python3
"""
Quick test script for image analysis via HTTP server.

Usage:
    python test_image_quick.py

Requirements:
    - Holmes server running at http://localhost:8080
    - OPENAI_API_KEY environment variable set
    - Vision-enabled model access (e.g., gpt-4o)
"""

import sys

import requests


def test_simple_image() -> bool:
    """Test with a simple public image URL."""
    url = "http://localhost:8080/api/chat"

    # Use a small public domain image from Wikimedia Commons
    payload = {
        "ask": "What's in this image? Describe what you see.",
        "model": "gpt-4o",  # Vision-enabled model
        "images": [
            "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/800px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
        ],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant that analyzes images."}
        ],
    }

    print("Testing image analysis endpoint...")
    print("-" * 60)
    print(f"URL: {url}")
    print(f"Model: {payload['model']}")
    print(f"Image: {payload['images'][0][:80]}...")
    print("-" * 60)

    try:
        print("\nSending request...")
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        print("\n✓ SUCCESS!")
        print("-" * 60)
        print("Analysis:")
        print(data["analysis"])
        print("-" * 60)
        print(f"\nConversation history: {len(data['conversation_history'])} messages")

        # Check message structure
        user_messages = [
            msg for msg in data["conversation_history"] if msg.get("role") == "user"
        ]
        if user_messages:
            last_msg = user_messages[-1]
            content = last_msg.get("content")
            if isinstance(content, list):
                images = [c for c in content if c.get("type") == "image_url"]
                print(f"User message contains {len(images)} image(s) ✓")
            else:
                print("⚠ Warning: User message content is not an array")

        return True

    except requests.exceptions.ConnectionError:
        print("\n✗ ERROR: Cannot connect to server at http://localhost:8080")
        print("\nMake sure the server is running:")
        print("  poetry run python server.py")
        return False
    except requests.exceptions.Timeout:
        print("\n✗ ERROR: Request timed out")
        print("\nServer may be overloaded or image processing is slow")
        return False
    except requests.exceptions.HTTPError as e:
        print(f"\n✗ HTTP ERROR: {e}")
        if e.response is not None:
            print("\nResponse:")
            print(e.response.text[:500])
        return False
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        return False


def test_base64_image() -> bool:
    """Test with a tiny base64-encoded image."""
    url = "http://localhost:8080/api/chat"

    # 1x1 red pixel PNG
    tiny_image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="

    payload = {
        "ask": "What color is this image?",
        "model": "gpt-4o",
        "images": [tiny_image],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
    }

    print("\n\nTesting base64 image...")
    print("-" * 60)
    print(f"Image: data URI (1x1 pixel)")
    print("-" * 60)

    try:
        print("\nSending request...")
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        print("\n✓ SUCCESS!")
        print("-" * 60)
        print("Analysis:")
        print(data["analysis"])
        print("-" * 60)

        return True

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        if hasattr(e, "response") and e.response is not None:
            print("\nResponse:")
            print(e.response.text[:500])
        return False


def test_dict_format() -> bool:
    """Test with dict format including detail parameter."""
    url = "http://localhost:8080/api/chat"

    payload = {
        "ask": "Analyze this image in detail.",
        "model": "gpt-4o",
        "images": [
            {
                "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/800px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg",
                "detail": "high",  # Request high-detail analysis
            }
        ],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
    }

    print("\n\nTesting dict format with detail parameter...")
    print("-" * 60)
    print("Image format: dict with 'detail': 'high'")
    print("-" * 60)

    try:
        print("\nSending request...")
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        print("\n✓ SUCCESS!")
        print("-" * 60)
        print("Analysis:")
        print(data["analysis"][:300], "...")
        print("-" * 60)

        return True

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        if hasattr(e, "response") and e.response is not None:
            print("\nResponse:")
            print(e.response.text[:500])
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Holmes Image Analysis Quick Test")
    print("=" * 60)
    print("\nPrerequisites:")
    print("1. Server running: poetry run python server.py")
    print("2. OPENAI_API_KEY environment variable set")
    print("3. Access to vision model (gpt-4o, claude-3.5-sonnet, etc.)")
    print("\n" + "=" * 60)

    results = []

    # Run tests
    results.append(("Simple image URL", test_simple_image()))
    results.append(("Base64 data URI", test_base64_image()))
    results.append(("Dict format with detail", test_dict_format()))

    # Summary
    print("\n\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
    print("=" * 60)

    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed}/{total} passed")

    if passed < total:
        print("\n💡 TIP: Check server logs for detailed INFO messages about image processing")
        print("     Look for: 'Request includes N image(s)', 'Processing N image(s)', etc.")

    sys.exit(0 if passed == total else 1)
