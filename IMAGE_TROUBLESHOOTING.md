# Image Analysis Troubleshooting Guide

## Overview

This guide helps troubleshoot image analysis issues with the Holmes HTTP server.

## Comprehensive Logging Added

INFO-level logging has been added at multiple points in the image processing pipeline:

### 1. HTTP Endpoint (`server.py`)
When a request arrives with images:
```
INFO: Received /api/chat request with model: gpt-4o
INFO: Request includes 2 image(s)
INFO:   Image 1: string format - https://example.com/image1.png
INFO:   Image 2: dict format with keys: ['url', 'detail']
```

### 2. Message Building (`conversations.py`)
When building messages with images:
```
INFO: build_chat_messages: Processing 2 image(s) for vision model
INFO:   Image 1: Adding string image - https://example.com/image1.png
INFO:   Image 2: Adding dict image - url=data:image/jpeg;base64,..., keys=['url', 'detail']
INFO:     - detail: high
INFO: build_chat_messages: Built user message with 3 content items (1 text + 2 images)
```

### 3. LLM Call Preparation (`server.py`)
Before calling the LLM:
```
INFO: Calling LLM with 2 messages (streaming=False)
INFO:   Found 1 message(s) with structured content (potentially including images)
INFO: Starting non-streaming response
```

### 4. LLM Execution (`tool_calling_llm.py`)
When sending to the model:
```
INFO: LLM call iteration 1: Message 1 contains 2 image(s)
INFO:   Image 1: https://example.com/image1.png...
INFO:   Image 2: data:image/jpeg;base64,/9j/4AAQSkZJRg==...
```

## Quick Test Script

Use this script to test image analysis with your running server:

```python
#!/usr/bin/env python3
"""Quick test for image analysis via HTTP server."""

import requests
import sys

def test_simple_image():
    """Test with a simple public image URL."""
    url = "http://localhost:8080/api/chat"

    payload = {
        "ask": "What's in this image?",
        "model": "gpt-4o",  # Use a vision-enabled model
        "images": [
            "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/800px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
        ],
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ]
    }

    print("Sending request with image...")
    print(f"Image URL: {payload['images'][0][:80]}...")

    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        print("\n✓ Success!")
        print(f"Analysis: {data['analysis'][:200]}...")

        return True
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text[:500]}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Holmes Image Analysis Quick Test")
    print("=" * 60)
    print("\nMake sure:")
    print("1. Holmes server is running: poetry run python server.py")
    print("2. OPENAI_API_KEY is set")
    print("3. Check server logs for detailed INFO messages")
    print()

    success = test_simple_image()
    sys.exit(0 if success else 1)
```

## Troubleshooting Steps

### 1. Check Server Logs

Start the server and watch the logs:
```bash
poetry run python server.py
```

Look for the INFO messages listed above. They will show you:
- Whether images are being received by the endpoint
- How images are being processed in message building
- What's being sent to the LLM

### 2. Verify Image Format

The server accepts two formats:

**Simple string (URL or data URI):**
```json
{
  "images": [
    "https://example.com/image.jpg",
    "data:image/jpeg;base64,/9j/4AAQ..."
  ]
}
```

**Dict with optional parameters:**
```json
{
  "images": [
    {
      "url": "https://example.com/image.jpg",
      "detail": "high",
      "format": "image/jpeg"
    }
  ]
}
```

### 3. Check Model Compatibility

Ensure you're using a vision-enabled model:
- OpenAI: `gpt-4o`, `gpt-4-vision-preview`, `gpt-4o-mini`
- Anthropic: `claude-3.5-sonnet`, `claude-3-opus`, `claude-3-haiku`
- Google: `gemini-1.5-pro`, `gemini-1.5-flash`

### 4. Test with curl

Test the endpoint directly:
```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "ask": "What is in this image?",
    "model": "gpt-4o",
    "images": ["https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/800px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"],
    "conversation_history": [{"role": "system", "content": "You are a helpful assistant."}]
  }'
```

### 5. Common Issues

**Images not being sent to LLM:**
- Check if you see "Request includes N image(s)" in logs
- Verify "build_chat_messages: Processing N image(s)" appears
- Look for "Message N contains N image(s)" before LLM call

**Invalid image format:**
- If you see "Image dict must contain a 'url' key", your dict is missing the required url field
- Check that base64 data URIs start with "data:image/..."

**Model errors:**
- If the model doesn't support vision, you'll get an error from LiteLLM
- Try a different vision-enabled model

**Network issues:**
- Ensure image URLs are publicly accessible
- For base64, check the data isn't truncated

## Integration Test

To run the full integration test suite:

```bash
# Set environment variable to enable integration tests
export IMAGE_ANALYSIS_INTEGRATION=true

# Start the server in another terminal
poetry run python server.py

# Run integration tests
poetry run pytest test_image_analysis.py -v
```

## What the Logs Tell You

| Log Message | What It Means | If Missing |
|------------|---------------|------------|
| "Request includes N image(s)" | Images received by endpoint | Check client payload |
| "build_chat_messages: Processing N image(s)" | Images being formatted for LLM | Check message building logic |
| "Built user message with N content items" | Message structure created | Check content array building |
| "Found N message(s) with structured content" | Images detected before LLM call | Check message structure |
| "Message N contains N image(s)" | Images being sent to LLM | Check LLM input preparation |

## Next Steps

If images still aren't working after checking the logs:

1. Share the relevant log output (search for "image" in the logs)
2. Verify your model supports vision (check LiteLLM docs)
3. Test with a simple public image URL first
4. Check API key has vision model access
5. Look for any error messages in the LiteLLM layer
