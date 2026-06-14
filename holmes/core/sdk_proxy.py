"""LiteLLM proxy wrapper for the Claude Agent SDK engine.

The claude CLI (Claude Code) talks the Anthropic Messages API; we bridge it to a
provider (e.g. OpenRouter) through a local LiteLLM proxy. Newer CLI versions send
a top-level ``context_management`` field on /v1/messages (the Anthropic
"context editing" beta — e.g. ``clear_thinking`` edits, microcompact). LiteLLM
1.83.7's translation for non-Anthropic backends rejects unknown top-level fields
with HTTP 400 "context_management: Extra inputs are not permitted", which crashes
every multi-turn investigation once the context grows enough to trip the feature.

Rather than chase the (minified, statsig-gated) CLI code paths that add the
field, we strip it at the proxy boundary: a Starlette middleware removes
``context_management`` (and other known-unsupported top-level keys) from the
request body before LiteLLM parses it. This is provider/version independent.

Run with: ``python -m holmes.core.sdk_proxy --config <yaml> --port <p> --host <h>``
(the LiteLLM proxy app is imported in-process and served via uvicorn).
"""

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Top-level Anthropic Messages API fields that LiteLLM 1.83.7's non-Anthropic
# backend translation does not accept and that are safe to drop for evals.
_STRIP_TOP_LEVEL_KEYS = ("context_management",)


class _SanitizeASGIMiddleware:
    """Pure-ASGI middleware that rewrites POST /v1/messages request bodies.

    Implemented at the ASGI layer (not Starlette's BaseHTTPMiddleware, whose
    receive-wrapping conflicts with replacing the body) so it can buffer the
    request body, strip unsupported top-level fields, and feed the modified
    body downstream without breaking streaming responses.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").endswith("/v1/messages")
        ):
            return await self.app(scope, receive, send)

        # Buffer the full request body.
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return await self.app(scope, receive, send)

        new_body = _sanitize_body(body)
        out_body = new_body if new_body is not None else body

        # Fix Content-Length so the downstream app reads the right length.
        if new_body is not None:
            headers = [
                (k, v)
                for (k, v) in scope.get("headers", [])
                if k.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(out_body)).encode()))
            scope = {**scope, "headers": headers}

        sent = False

        async def wrapped_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": out_body, "more_body": False}
            return await receive()

        return await self.app(scope, wrapped_receive, send)


def _install_sanitizer(app) -> None:
    app.add_middleware(_SanitizeASGIMiddleware)


def _sanitize_body(raw: bytes):
    """Return a body with unsupported top-level keys removed, or None to keep."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    removed = [k for k in _STRIP_TOP_LEVEL_KEYS if k in data]
    if not removed:
        return None
    for k in removed:
        data.pop(k, None)
    msg = "sdk_proxy: stripped unsupported request field(s): " + ", ".join(removed)
    logger.info(msg)
    print(msg, file=sys.stderr, flush=True)
    return json.dumps(data).encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # LiteLLM's proxy app reads the model_list config from CONFIG_FILE_PATH at
    # startup; set it before importing the app.
    os.environ["CONFIG_FILE_PATH"] = args.config

    import uvicorn
    from litellm.proxy.proxy_server import app

    _install_sanitizer(app)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
