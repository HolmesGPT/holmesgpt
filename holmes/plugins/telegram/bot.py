"""Telegram front-end for a running HolmesGPT server.

The bot long-polls Telegram (outbound HTTPS only, no inbound port) and forwards every private message from an
allowed chat to ``POST /api/chat`` of the Holmes server. Optionally (see ``checks.py``) it also runs scheduled
health checks and pushes the ones that are not OK.

Configuration (environment variables):

==========================  ======================================================================
TELEGRAM_BOT_TOKEN          Bot token from @BotFather (required)
TELEGRAM_ALLOWED_CHAT_IDS   Comma separated chat ids allowed to talk to the bot (required)
HOLMES_URL                  Holmes server base URL (default ``http://localhost:8080``)
HOLMES_API_KEY              Sent as ``Authorization: Bearer`` when set
TELEGRAM_CHECKS_DIR         Directory with ``schedule.json`` + prompts. Unset = no scheduled checks
TELEGRAM_REQUEST_TIMEOUT    Seconds to wait for a chat answer (default 300)
TELEGRAM_CHECK_TIMEOUT      Seconds to wait for a check answer (default 900)
TELEGRAM_MAX_HISTORY_BYTES  Conversation reset threshold per chat (default 300000)
==========================  ======================================================================
"""

import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from holmes.plugins.telegram.checks import (
    Check,
    CheckScheduler,
    ChecksConfig,
    load_checks,
    run_check,
)
from holmes.plugins.telegram.formatting import (
    TELEGRAM_MESSAGE_LIMIT,
    markdown_to_telegram_html,
    split_message,
)

logger = logging.getLogger(__name__)
DENIED_REPLY_INTERVAL = 600


@dataclass
class TelegramBotConfig:
    bot_token: str
    allowed_chat_ids: set[int]
    holmes_url: str = "http://localhost:8080"
    holmes_api_key: str = ""
    checks_dir: str = ""
    request_timeout: int = 300
    check_timeout: int = 900
    max_history_bytes: int = 300_000

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "TelegramBotConfig":
        env = dict(os.environ) if env is None else env
        token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        raw_ids = env.get("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "")
        allowed = {int(item) for item in raw_ids.split(",") if item}
        if not allowed:
            # An open bot would let anyone on Telegram run a read-only investigation (and spend LLM tokens).
            raise ValueError(
                "TELEGRAM_ALLOWED_CHAT_IDS is required (comma separated chat ids)"
            )
        return cls(
            bot_token=token,
            allowed_chat_ids=allowed,
            holmes_url=env.get("HOLMES_URL", "http://localhost:8080").rstrip("/"),
            holmes_api_key=env.get("HOLMES_API_KEY", ""),
            checks_dir=env.get("TELEGRAM_CHECKS_DIR", ""),
            request_timeout=int(env.get("TELEGRAM_REQUEST_TIMEOUT", "300")),
            check_timeout=int(env.get("TELEGRAM_CHECK_TIMEOUT", "900")),
            max_history_bytes=int(env.get("TELEGRAM_MAX_HISTORY_BYTES", "300000")),
        )


class TelegramAPI:
    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}"

    def call(
        self, method: str, payload: dict[str, Any], timeout: float = 60
    ) -> dict[str, Any]:
        # Never log the URL or the exception text: both can contain the bot token.
        response = requests.post(
            f"{self._base}/{method}", json=payload, timeout=timeout
        )
        response.raise_for_status()
        return response.json()

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        data = self.call(
            "getUpdates",
            {"offset": offset, "timeout": 50, "allowed_updates": ["message"]},
            timeout=65,
        )
        return data.get("result", [])

    def send(self, chat_id: int, text: str, markdown: bool = True) -> None:
        """Send ``text`` split in Telegram-sized messages. If the HTML is rejected, resend it as plain text."""
        for part in split_message(text.strip() or "(empty answer)"):
            try:
                if not markdown:
                    raise ValueError("plain text requested")
                self.call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": markdown_to_telegram_html(part)[
                            :TELEGRAM_MESSAGE_LIMIT
                        ],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
            except Exception:  # noqa: BLE001
                try:
                    self.call(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": part[:TELEGRAM_MESSAGE_LIMIT],
                            "disable_web_page_preview": True,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "sendMessage failed chat=%s: %s", chat_id, type(exc).__name__
                    )

    def typing(self, chat_id: int) -> None:
        try:
            self.call(
                "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("sendChatAction failed: %s", type(exc).__name__)


class HolmesAPI:
    def __init__(self, config: TelegramBotConfig):
        self._config = config

    def chat(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        headers = {}
        if self._config.holmes_api_key:
            headers["Authorization"] = f"Bearer {self._config.holmes_api_key}"
        response = requests.post(
            f"{self._config.holmes_url}/api/chat",
            json=body,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


@dataclass
class HolmesTelegramBot:
    config: TelegramBotConfig
    telegram: TelegramAPI
    holmes: HolmesAPI
    checks: ChecksConfig | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    _history: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    _locks: dict[int, threading.Lock] = field(default_factory=dict)
    _last_denied: dict[int, float] = field(default_factory=dict)
    _last_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # One check at a time: each one runs many tool calls against the cluster and the LLM.
    _check_lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- chat ----------------------------------------------------------------------------------------------

    def _typing_loop(self, chat_id: int, done: threading.Event) -> None:
        while not done.wait(4):
            self.telegram.typing(chat_id)

    def ask(self, chat_id: int, question: str) -> str:
        body: dict[str, Any] = {"ask": question}
        if chat_id in self._history:
            body["conversation_history"] = self._history[chat_id]
        data = self.holmes.chat(body, self.config.request_timeout)
        if data.get("conversation_history") is not None:
            self._history[chat_id] = data["conversation_history"]
        return data.get("analysis") or json.dumps(data)[:3500]

    def _answer(self, chat_id: int, text: str) -> None:
        lock = self._locks.setdefault(chat_id, threading.Lock())
        if not lock.acquire(blocking=False):
            self.telegram.send(
                chat_id,
                "Still working on the previous question, please wait for the answer.",
            )
            return
        done = threading.Event()
        threading.Thread(
            target=self._typing_loop, args=(chat_id, done), daemon=True
        ).start()
        try:
            history = self._history.get(chat_id)
            if history and len(json.dumps(history)) > self.config.max_history_bytes:
                self._history.pop(chat_id, None)
                self.telegram.send(
                    chat_id, "The conversation got too long: it was reset."
                )
            self.telegram.send(chat_id, self.ask(chat_id, text))
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            self.telegram.send(chat_id, f"Holmes answered with HTTP error {code}.")
        except Exception as exc:  # noqa: BLE001
            self.telegram.send(
                chat_id, f"Error contacting Holmes: {type(exc).__name__}."
            )
        finally:
            done.set()
            lock.release()

    # ---- updates -------------------------------------------------------------------------------------------

    def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if not isinstance(chat_id, int) or chat.get("type") != "private" or not text:
            return  # groups, channels and non-text messages are ignored
        command = text.split()[0].split("@")[0].lower()
        if command == "/id":
            self.telegram.send(chat_id, f"Your chat id is {chat_id}.", markdown=False)
            return
        if chat_id not in self.config.allowed_chat_ids:
            now = time.time()
            if now - self._last_denied.get(chat_id, 0) > DENIED_REPLY_INTERVAL:
                self._last_denied[chat_id] = now
                self.telegram.send(
                    chat_id,
                    f"Not authorized. Your chat id is {chat_id}.",
                    markdown=False,
                )
            logger.info("denied chat=%s", chat_id)
            return
        if command in ("/start", "/help"):
            self.telegram.send(chat_id, self.help_text(), markdown=False)
        elif command == "/reset":
            self._history.pop(chat_id, None)
            self.telegram.send(chat_id, "Conversation reset.", markdown=False)
        elif command == "/check" and self.checks is not None:
            parts = text.split(None, 1)
            self.command_check(chat_id, parts[1].strip() if len(parts) > 1 else "")
        else:
            logger.info(
                "question chat=%s chars=%d", chat_id, len(text)
            )  # contents are never logged
            threading.Thread(
                target=self._answer, args=(chat_id, text), daemon=True
            ).start()

    def help_text(self) -> str:
        lines = [
            "Ask a question about your infrastructure and HolmesGPT investigates it.",
            "/reset clears the conversation.",
            "/id shows your chat id.",
        ]
        if self.checks is not None:
            lines.insert(
                2, "/check lists the scheduled checks, /check <name> runs one now."
            )
        return "\n".join(lines)

    # ---- scheduled checks (optional) ------------------------------------------------------------------------

    def command_check(self, chat_id: int, arg: str) -> None:
        assert self.checks is not None
        by_name = {c.name: c for c in self.checks.checks}
        if not arg:
            lines = [f"Scheduled checks (times in {self.checks.timezone.key}):"]
            for check in self.checks.checks:
                last = self._last_runs.get(check.name)
                suffix = f"; last {last['when']} {last['severity']}" if last else ""
                lines.append(f"- {check.name}: {', '.join(check.times)}{suffix}")
            lines.append("Use /check <name> to run one now.")
            self.telegram.send(chat_id, "\n".join(lines), markdown=False)
            return
        check = by_name.get(arg)
        if check is None:
            self.telegram.send(
                chat_id, "Unknown check. Use /check for the list.", markdown=False
            )
            return
        self.telegram.send(
            chat_id, f"Running {check.name}: it takes 1-5 minutes.", markdown=False
        )
        threading.Thread(
            target=self.execute_check, args=(check, True, chat_id), daemon=True
        ).start()

    def execute_check(
        self, check: Check, force: bool = False, chat_id: int | None = None
    ) -> None:
        """Run a check. Scheduled runs notify only when not OK; ``force`` (/check <name>) always replies."""
        assert self.checks is not None
        with self._check_lock:
            result = run_check(
                check,
                ask=lambda prompt: self.holmes.chat(
                    {"ask": prompt}, self.config.check_timeout
                ),
                retries=self.checks.retries,
                retry_delay=self.checks.retry_delay,
            )
        self._last_runs[check.name] = {
            "when": time.strftime("%H:%M UTC", time.gmtime()),
            "severity": result.severity,
        }
        logger.info(
            "check %s: %s tools=%d secs=%.0f",
            check.name,
            result.severity,
            result.tool_calls,
            result.seconds,
        )
        if result.severity == "OK" and not force:
            return  # healthy: stay silent
        head, _, rest = result.text[:12000].partition("\n")
        message = f"**{head}**" + (f"\n{rest}" if rest else "")
        for target in [chat_id] if chat_id else sorted(self.config.allowed_chat_ids):
            self.telegram.send(target, message)

    # ---- main loop ------------------------------------------------------------------------------------------

    def poll(self) -> None:
        offset, backoff = 0, 1
        while not self.stop.is_set():
            try:
                for update in self.telegram.get_updates(offset):
                    offset = update["update_id"] + 1
                    if "message" in update:
                        self.handle_message(update["message"])
                backoff = 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("getUpdates failed: %s", type(exc).__name__)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def start(self) -> None:
        if self.checks is not None:
            scheduler = CheckScheduler(
                config=self.checks, run=self.execute_check, stop=self.stop
            )
            threading.Thread(target=scheduler.loop, daemon=True).start()
            logger.info(
                "scheduled checks enabled: %s",
                ", ".join(c.name for c in self.checks.checks),
            )
        self.poll()


def run() -> None:
    """Entry point of ``holmes-telegram``."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # httpx/urllib3 style loggers would print the request URL, which contains the bot token.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    config = TelegramBotConfig.from_env()
    checks = load_checks(config.checks_dir) if config.checks_dir else None
    bot = HolmesTelegramBot(
        config=config,
        telegram=TelegramAPI(config.bot_token),
        holmes=HolmesAPI(config),
        checks=checks,
    )
    signal.signal(signal.SIGTERM, lambda *_: bot.stop.set())
    signal.signal(signal.SIGINT, lambda *_: bot.stop.set())
    bot.start()


if __name__ == "__main__":
    run()
