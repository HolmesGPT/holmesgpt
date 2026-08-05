import logging
import os
from typing import Any, Dict, Iterable, List, Optional

import requests
from pydantic import BaseModel, Field
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_never,
    wait_exponential,
)

TELEGRAM_MESSAGE_LIMIT = 4096
logger = logging.getLogger(__name__)


class TelegramBotConfig(BaseModel):
    bot_token: str
    holmes_api_url: str = "http://localhost:8080"
    allowed_chat_ids: set[int] = Field(default_factory=set)
    poll_timeout_seconds: int = Field(default=30, ge=1, le=50)
    request_timeout_seconds: int = Field(default=120, ge=1, le=600)
    history_messages: int = Field(default=30, ge=2, le=200)


class TelegramAPI:
    def __init__(self, config: TelegramBotConfig):
        self.config = config
        self.base_url = f"https://api.telegram.org/bot{config.bot_token}"

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_never,
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def get_updates(self, offset: Optional[int]) -> List[Dict[str, Any]]:
        response = requests.get(
            f"{self.base_url}/getUpdates",
            params={
                "offset": offset,
                "timeout": self.config.poll_timeout_seconds,
                "allowed_updates": '["message"]',
            },
            timeout=self.config.poll_timeout_seconds + 10,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")
        return payload.get("result", [])

    def send_message(self, chat_id: int, text: str) -> None:
        for chunk in split_telegram_message(text):
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram sendMessage failed: {payload}")


class HolmesAPI:
    def __init__(self, config: TelegramBotConfig):
        self.config = config

    def ask(
        self,
        prompt: str,
        *,
        chat_id: int,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        response = requests.post(
            f"{self.config.holmes_api_url.rstrip('/')}/api/chat",
            json={
                "ask": prompt,
                "conversation_history": history,
                "stream": False,
                "user_id": f"telegram:{chat_id}",
                "conversation_id": f"telegram:{chat_id}",
                "request_type": "telegram_chat",
                "request_source": "telegram",
            },
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("analysis"), str):
            raise RuntimeError(f"Holmes returned an invalid chat response: {payload}")
        return payload


class HolmesTelegramBot:
    def __init__(
        self,
        config: TelegramBotConfig,
        telegram: Optional[TelegramAPI] = None,
        holmes: Optional[HolmesAPI] = None,
    ):
        self.config = config
        self.telegram = telegram or TelegramAPI(config)
        self.holmes = holmes or HolmesAPI(config)
        self._histories: Dict[int, List[Dict[str, Any]]] = {}

    def handle_update(self, update: Dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text")
        if not isinstance(chat_id, int) or not isinstance(text, str):
            return
        if self.config.allowed_chat_ids and chat_id not in self.config.allowed_chat_ids:
            self.telegram.send_message(
                chat_id, "This chat is not allowed to use HolmesGPT."
            )
            return

        prompt = text.strip()
        if prompt in {"/start", "/help"}:
            self.telegram.send_message(
                chat_id,
                "Send an infrastructure question. "
                "Use /reset to clear this chat's context.",
            )
            return
        if prompt == "/reset":
            self._histories.pop(chat_id, None)
            self.telegram.send_message(chat_id, "Conversation context cleared.")
            return
        if not prompt:
            return

        try:
            response = self.holmes.ask(
                prompt,
                chat_id=chat_id,
                history=self._histories.get(chat_id),
            )
            history = response.get("conversation_history")
            if isinstance(history, list):
                self._histories[chat_id] = trim_conversation_history(
                    history, self.config.history_messages
                )
            self.telegram.send_message(chat_id, response["analysis"])
        except requests.exceptions.RequestException as error:
            logging.exception("Telegram request failed")
            self.telegram.send_message(chat_id, f"HolmesGPT request failed: {error}")
        except Exception as error:
            logging.exception("Telegram update failed")
            self.telegram.send_message(chat_id, f"HolmesGPT could not answer: {error}")

    def run(self) -> None:
        offset: Optional[int] = None
        logging.info("HolmesGPT Telegram bot started")
        while True:
            updates = self.telegram.get_updates(offset)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                self.handle_update(update)


def split_telegram_message(text: str) -> Iterable[str]:
    remaining = text or "(empty response)"
    while len(remaining) > TELEGRAM_MESSAGE_LIMIT:
        split_at = remaining.rfind("\n", 0, TELEGRAM_MESSAGE_LIMIT + 1)
        if split_at <= 0:
            split_at = TELEGRAM_MESSAGE_LIMIT
        yield remaining[:split_at]
        remaining = remaining[split_at:].lstrip("\n")
    yield remaining


def trim_conversation_history(
    history: List[Dict[str, Any]], max_messages: int
) -> List[Dict[str, Any]]:
    if len(history) <= max_messages:
        return history
    first = history[0]
    if first.get("role") == "system":
        return [first, *history[-(max_messages - 1) :]]
    return history[-max_messages:]


def run() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    allowed = {
        int(value.strip())
        for value in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
        if value.strip()
    }
    config = TelegramBotConfig(
        bot_token=token,
        holmes_api_url=os.environ.get("HOLMES_API_URL", "http://localhost:8080"),
        allowed_chat_ids=allowed,
        poll_timeout_seconds=int(
            os.environ.get("TELEGRAM_POLL_TIMEOUT_SECONDS", "30")
        ),
        request_timeout_seconds=int(
            os.environ.get("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "120")
        ),
        history_messages=int(os.environ.get("TELEGRAM_HISTORY_MESSAGES", "30")),
    )
    HolmesTelegramBot(config).run()


if __name__ == "__main__":
    run()
