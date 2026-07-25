from unittest.mock import MagicMock, patch

from holmes.plugins.telegram.bot import (
    HolmesTelegramBot,
    TelegramBotConfig,
    run,
    split_telegram_message,
    trim_conversation_history,
)


def test_forbidden_chat_is_rejected_without_calling_holmes():
    telegram = MagicMock()
    holmes = MagicMock()
    bot = HolmesTelegramBot(
        TelegramBotConfig(bot_token="token", allowed_chat_ids={42}),
        telegram=telegram,
        holmes=holmes,
    )

    bot.handle_update({"message": {"chat": {"id": 7}, "text": "What failed?"}})

    telegram.send_message.assert_called_once_with(
        7, "This chat is not allowed to use HolmesGPT."
    )
    holmes.ask.assert_not_called()


def test_question_is_forwarded_and_conversation_history_is_reused():
    telegram = MagicMock()
    holmes = MagicMock()
    holmes.ask.side_effect = [
        {
            "analysis": "The first answer",
            "conversation_history": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "The first answer"},
            ],
        },
        {
            "analysis": "The follow-up answer",
            "conversation_history": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "follow-up"},
                {"role": "assistant", "content": "The follow-up answer"},
            ],
        },
    ]
    bot = HolmesTelegramBot(
        TelegramBotConfig(bot_token="token"), telegram=telegram, holmes=holmes
    )

    bot.handle_update({"message": {"chat": {"id": 42}, "text": "first"}})
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "follow-up"}})

    assert holmes.ask.call_args_list[0].kwargs["history"] is None
    assert holmes.ask.call_args_list[1].kwargs["history"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "The first answer"},
    ]
    assert telegram.send_message.call_args_list[-1].args == (
        42,
        "The follow-up answer",
    )


def test_reset_clears_conversation_history():
    telegram = MagicMock()
    holmes = MagicMock(
        return_value={
            "analysis": "answer",
            "conversation_history": [{"role": "system", "content": "system"}],
        }
    )
    holmes.ask.return_value = {
        "analysis": "answer",
        "conversation_history": [{"role": "system", "content": "system"}],
    }
    bot = HolmesTelegramBot(
        TelegramBotConfig(bot_token="token"), telegram=telegram, holmes=holmes
    )
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "question"}})

    bot.handle_update({"message": {"chat": {"id": 42}, "text": "/reset"}})
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "new question"}})

    assert holmes.ask.call_args_list[-1].kwargs["history"] is None
    assert telegram.send_message.call_args_list[-2].args == (
        42,
        "Conversation context cleared.",
    )


def test_long_message_is_split_on_newline_within_telegram_limit():
    text = ("a" * 3000) + "\n" + ("b" * 3000)

    chunks = list(split_telegram_message(text))

    assert chunks == ["a" * 3000, "b" * 3000]


def test_trimmed_history_preserves_required_system_message():
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "new answer"},
    ]

    trimmed = trim_conversation_history(history, 3)

    assert trimmed == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "new answer"},
    ]


def test_run_reads_deployment_configuration_from_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("HOLMES_API_URL", "http://holmes:80")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42,-1007")
    monkeypatch.setenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("TELEGRAM_HISTORY_MESSAGES", "40")

    with patch("holmes.plugins.telegram.bot.HolmesTelegramBot") as bot_class:
        run()

    config = bot_class.call_args.args[0]
    assert config == TelegramBotConfig(
        bot_token="secret-token",
        holmes_api_url="http://holmes:80",
        allowed_chat_ids={42, -1007},
        poll_timeout_seconds=20,
        request_timeout_seconds=180,
        history_messages=40,
    )
    bot_class.return_value.run.assert_called_once_with()
