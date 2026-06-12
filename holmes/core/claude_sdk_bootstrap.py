"""Bootstrap the Claude Agent SDK runtime so evals can run on the SDK engine.

The SDK engine needs three things at runtime that the baseline engine doesn't:
the `claude` CLI on PATH, the `claude-agent-sdk` Python package, and an
Anthropic-Messages-API endpoint to talk to. In CI the eval workflow already
writes a Holmes model_list.yaml; this module converts it into a LiteLLM proxy
config, starts the proxy (exposing /v1/messages), and points the SDK at it via
ANTHROPIC_BASE_URL — all idempotently, so it can be called from a pytest
session hook without the eval workflow itself needing to know about the engine.

Activation (checked by the pytest conftest):
  * env HOLMES_ENGINE=claude-sdk, or
  * a committed flag file tests/llm/.claude_sdk_engine on the branch.
"""

import logging
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROXY_HOST = "127.0.0.1"
PROXY_PORT = int(os.environ.get("HOLMES_SDK_PROXY_PORT", "4001"))
PROXY_CONFIG = "/tmp/holmes_sdk_litellm_proxy.yaml"
PROXY_LOG = "/tmp/holmes_sdk_litellm_proxy.log"
FLAG_FILE = Path(__file__).resolve().parents[2] / "tests" / "llm" / ".claude_sdk_engine"

_ENV_REF_RE = re.compile(r"\{\{\s*env\.([A-Z0-9_]+)\s*\}\}")


def engine_requested() -> bool:
    """True if the SDK engine should be used for this run."""
    if os.environ.get("HOLMES_ENGINE", "").lower() == "claude-sdk":
        return True
    return FLAG_FILE.is_file()


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _model_list_path() -> Optional[str]:
    p = os.environ.get("MODEL_LIST_FILE_LOCATION", "/tmp/model_list.yaml")
    return p if Path(p).is_file() else None


def _normalize_for_proxy(model: str, api_base: str) -> str:
    """Route OpenRouter-backed models through LiteLLM's native `openrouter/`
    provider for the Messages-API proxy.

    The model_list often uses `openai/<vendor>/<model>` (OpenAI-compat) because
    that's what the autoevals classifier needs. But the claude CLI hits the
    proxy's /v1/messages with tool definitions, and LiteLLM's openai-compat
    translation mishandles Anthropic tool-use round-trips (turns fail with an
    empty is_error result). The `openrouter/` provider handles it correctly.
    So when the endpoint is OpenRouter, strip a leading openai/ and prefix
    openrouter/.
    """
    if "openrouter.ai" not in (api_base or ""):
        return model
    if model.startswith("openrouter/"):
        return model
    # Target the `openai/<vendor>/<model>` form (e.g. openai/anthropic/claude-opus-4.6)
    # used so the autoevals classifier works; rewrite to openrouter/<vendor>/<model>.
    # Leave plain `openai/<model>` (no vendor segment) untouched.
    if model.startswith("openai/") and model.count("/") >= 2:
        return "openrouter/" + model[len("openai/"):]
    return model


def _write_proxy_config(model_list_path: str) -> list:
    import yaml

    src = yaml.safe_load(Path(model_list_path).read_text()) or {}
    ml = []
    for name, entry in src.items():
        if not isinstance(entry, dict):
            continue
        lp = {}
        for k, v in entry.items():
            if isinstance(v, str):
                v = _ENV_REF_RE.sub(r"os.environ/\1", v)
            lp[k] = v
        api_base = lp.get("api_base", "")
        if "model" in lp:
            normalized = _normalize_for_proxy(lp["model"], api_base)
            if normalized != lp["model"]:
                lp["model"] = normalized
                # openrouter/ provider uses its own base + OPENROUTER_API_KEY;
                # a leftover openai api_base would mis-route it.
                lp.pop("api_base", None)
                lp.setdefault("api_key", "os.environ/OPENROUTER_API_KEY")
        ml.append({"model_name": name, "litellm_params": lp})
    Path(PROXY_CONFIG).write_text(
        yaml.safe_dump({"model_list": ml, "litellm_settings": {"drop_params": True}})
    )
    return [m["model_name"] for m in ml]


def _ensure_cli() -> Optional[str]:
    """Ensure the claude CLI is available; return its path."""
    explicit = os.environ.get("HOLMES_CLAUDE_CLI_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    logger.info("Installing claude CLI (npm i -g @anthropic-ai/claude-code)...")
    try:
        subprocess.run(
            ["npm", "install", "-g", "@anthropic-ai/claude-code"],
            check=True, capture_output=True, text=True, timeout=300,
        )
    except Exception as e:
        logger.error(f"claude CLI install failed: {e}")
        return None
    return shutil.which("claude")


def _ensure_sdk_pkg() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
        return True
    except ImportError:
        logger.info("Installing claude-agent-sdk + litellm[proxy]...")
        try:
            subprocess.run(
                ["pip", "install", "-q", "claude-agent-sdk", "litellm[proxy]"],
                check=True, capture_output=True, text=True, timeout=600,
            )
            return True
        except Exception as e:
            logger.error(f"SDK package install failed: {e}")
            return False


def _start_proxy(probe_model: str) -> bool:
    if _port_open(PROXY_HOST, PROXY_PORT):
        logger.info(f"SDK proxy already listening on {PROXY_PORT}")
        return True
    model_list = _model_list_path()
    if not model_list:
        logger.error("No model_list.yaml found; cannot start SDK proxy.")
        return False
    models = _write_proxy_config(model_list)
    logger.info(f"Starting LiteLLM proxy for SDK engine (models: {models})")
    with open(PROXY_LOG, "w") as log_fh:
        subprocess.Popen(
            ["litellm", "--config", PROXY_CONFIG, "--port", str(PROXY_PORT), "--host", PROXY_HOST],
            stdout=log_fh, stderr=subprocess.STDOUT, start_new_session=True,
        )
    import json
    import urllib.request

    # Probe with a TOOL-USE request (not a trivial message): the claude CLI
    # always sends tool definitions, and the failure mode we must catch is the
    # provider/route mishandling Anthropic tool-use. A trivial probe would pass
    # while every real eval fails. We require the model to emit a tool_use block.
    probe_body = json.dumps({
        "model": probe_model, "max_tokens": 256,
        "tools": [{
            "name": "run_cmd", "description": "Run a shell command",
            "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
        }],
        "messages": [{"role": "user", "content": "Use the run_cmd tool to run: echo ok"}],
    }).encode()

    deadline = time.time() + 180
    last_detail = ""
    while time.time() < deadline:
        if _port_open(PROXY_HOST, PROXY_PORT):
            try:
                req = urllib.request.Request(
                    f"http://{PROXY_HOST}:{PROXY_PORT}/v1/messages",
                    data=probe_body,
                    headers={"content-type": "application/json", "x-api-key": "dummy",
                             "anthropic-version": "2023-06-01"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode())
                    blocks = payload.get("content") or []
                    if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks):
                        logger.info("SDK proxy handles tool-use (probe OK).")
                        return True
                    last_detail = f"no tool_use in probe response: {str(payload)[:300]}"
            except Exception as e:
                last_detail = f"{type(e).__name__}: {str(e)[:300]}"
        time.sleep(3)
    logger.error(
        f"SDK proxy tool-use probe failed ({last_detail}). Proxy log tail:\n"
        + _tail(PROXY_LOG)
    )
    return False


def _tail(path: str, n: int = 40) -> str:
    try:
        return "\n".join(Path(path).read_text().splitlines()[-n:])
    except Exception:
        return "(no log)"


def attach_sdk_env(timeout_s: int = 180) -> bool:
    """Worker-side: set engine env and wait for the controller's proxy.

    Does no installs and starts no proxy — assumes the xdist controller already
    ran ensure_sdk_runtime(). Used in xdist worker processes to avoid races.
    """
    cli = os.environ.get("HOLMES_CLAUDE_CLI_PATH") or shutil.which("claude")
    if cli:
        os.environ["HOLMES_CLAUDE_CLI_PATH"] = cli
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _port_open(PROXY_HOST, PROXY_PORT):
            break
        time.sleep(2)
    if not _port_open(PROXY_HOST, PROXY_PORT):
        logger.error("SDK proxy not reachable from worker.")
        return False
    os.environ["HOLMES_ENGINE"] = "claude-sdk"
    os.environ["ANTHROPIC_BASE_URL"] = f"http://{PROXY_HOST}:{PROXY_PORT}"
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-proxy-placeholder"
    os.environ.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    return True


def ensure_sdk_runtime(probe_model: str = "opus-4.6") -> bool:
    """Idempotently prepare the SDK runtime. Returns True on success.

    Sets HOLMES_ENGINE=claude-sdk and ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY in
    os.environ so the eval harness routes through the SDK engine.
    """
    cli = _ensure_cli()
    if not cli:
        return False
    os.environ["HOLMES_CLAUDE_CLI_PATH"] = cli
    if not _ensure_sdk_pkg():
        return False
    if not _start_proxy(probe_model):
        return False
    os.environ["HOLMES_ENGINE"] = "claude-sdk"
    os.environ["ANTHROPIC_BASE_URL"] = f"http://{PROXY_HOST}:{PROXY_PORT}"
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-proxy-placeholder"
    os.environ.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    return True
