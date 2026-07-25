# Telegram Bot

The `holmes-telegram` process connects a Telegram bot to a running HolmesGPT
HTTP server. It keeps per-chat conversation context in memory and forwards
questions to `/api/chat`.

## Setup

1. Create a bot with [BotFather](https://t.me/BotFather) and copy its token.
2. Start the HolmesGPT HTTP server.
3. Run the Telegram adapter:

```bash
export TELEGRAM_BOT_TOKEN="<bot token>"
export HOLMES_API_URL="http://localhost:8080"
export TELEGRAM_ALLOWED_CHAT_IDS="123456789,-1001234567890"
holmes-telegram
```

`TELEGRAM_ALLOWED_CHAT_IDS` is optional. When it is set, messages from other
private chats and groups are rejected. Use `/reset` in Telegram to clear the
in-memory conversation context for that chat.

The adapter uses Telegram long polling, so it does not require a public webhook
endpoint. Run only one polling adapter for a bot token.
