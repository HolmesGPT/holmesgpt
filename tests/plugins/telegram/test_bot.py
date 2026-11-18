from zoneinfo import ZoneInfo

import pytest

from holmes.plugins.telegram.bot import HolmesTelegramBot, TelegramBotConfig
from holmes.plugins.telegram.checks import Check, ChecksConfig


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text, markdown=True):
        self.sent.append((chat_id, text))

    def typing(self, chat_id):
        pass


class FakeHolmes:
    def __init__(self, analysis="answer"):
        self.calls = []
        self.analysis = analysis

    def chat(self, body, timeout):
        self.calls.append(body)
        return {"analysis": self.analysis, "conversation_history": [{"role": "tool"}]}


def config(**kw):
    return TelegramBotConfig(bot_token="t", allowed_chat_ids={1}, **kw)


def msg(chat_id, text, chat_type="private"):
    return {"chat": {"id": chat_id, "type": chat_type}, "text": text}


def make_bot(checks=None):
    return HolmesTelegramBot(
        config=config(), telegram=FakeTelegram(), holmes=FakeHolmes(), checks=checks
    )


def test_config_requires_token_and_allowlist():
    with pytest.raises(ValueError):
        TelegramBotConfig.from_env({"TELEGRAM_ALLOWED_CHAT_IDS": "1"})
    with pytest.raises(ValueError):
        TelegramBotConfig.from_env({"TELEGRAM_BOT_TOKEN": "t"})
    cfg = TelegramBotConfig.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_ALLOWED_CHAT_IDS": "1, -2",
            "HOLMES_URL": "http://h/",
        }
    )
    assert cfg.allowed_chat_ids == {1, -2} and cfg.holmes_url == "http://h"
    assert cfg.checks_dir == ""  # scheduled checks are off by default


def test_unauthorized_chat_gets_its_id_once_and_is_never_forwarded():
    bot = make_bot()
    bot.handle_message(msg(99, "hello"))
    bot.handle_message(msg(99, "hello again"))
    assert bot.holmes.calls == []
    assert len(bot.telegram.sent) == 1 and "99" in bot.telegram.sent[0][1]


def test_groups_and_empty_messages_are_ignored():
    bot = make_bot()
    bot.handle_message(msg(1, "hi", chat_type="group"))
    bot.handle_message(msg(1, ""))
    assert bot.telegram.sent == [] and bot.holmes.calls == []


def test_question_is_answered_and_history_kept(monkeypatch):
    bot = make_bot()
    bot._answer(1, "why is pod x down?")
    assert bot.telegram.sent == [(1, "answer")]
    bot._answer(1, "and y?")
    assert bot.holmes.calls[1]["conversation_history"] == [{"role": "tool"}]
    bot.handle_message(msg(1, "/reset"))
    assert 1 not in bot._history


def test_check_command_is_not_available_without_checks():
    bot = make_bot()
    assert "/check" not in bot.help_text()


def test_check_list_and_unknown_check():
    cfg = ChecksConfig(
        timezone=ZoneInfo("UTC"), checks=[Check("nodes", "p", ["08:00"])]
    )
    bot = make_bot(cfg)
    assert "/check" in bot.help_text()
    bot.command_check(1, "")
    assert "nodes: 08:00" in bot.telegram.sent[-1][1]
    bot.command_check(1, "nope")
    assert "Unknown check" in bot.telegram.sent[-1][1]


def test_scheduled_ok_check_is_silent_and_failures_are_pushed():
    cfg = ChecksConfig(
        timezone=ZoneInfo("UTC"), checks=[Check("nodes", "p", ["08:00"])], retries=0
    )
    bot = make_bot(cfg)
    bot.holmes.analysis = "✅ OK - nodes\nCoverage: complete"
    bot.execute_check(cfg.checks[0])
    assert bot.telegram.sent == []
    bot.holmes.analysis = "\U0001f6a8 CRITICAL - nodes\nnode down"
    bot.execute_check(cfg.checks[0])
    assert len(bot.telegram.sent) == 1 and bot.telegram.sent[0][0] == 1
    # /check <name> always replies, even when OK
    bot.holmes.analysis = "✅ OK - nodes"
    bot.execute_check(cfg.checks[0], force=True, chat_id=1)
    assert len(bot.telegram.sent) == 2
