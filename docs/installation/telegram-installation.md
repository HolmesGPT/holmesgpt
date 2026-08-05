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

## Kubernetes with Helm

Configure Telegram in Helm values in the same way as other messaging
integrations: keep the bot token in a Kubernetes Secret and reference it from
the chart.

```bash
kubectl create secret generic holmes-telegram \
  --from-literal=bot-token="<bot token>" \
  --namespace holmes
```

```yaml
telegram:
  enabled: true
  existingSecret:
    name: holmes-telegram
    key: bot-token
  allowedChatIds:
    - 123456789
    - -1001234567890
```

The chart runs one Telegram polling replica independently of the Holmes API
replica count. By default it connects to the in-cluster Holmes Service created
by the same Helm release. Use `telegram.holmesApiUrl` only when the bot should
connect to a different Holmes server.

Optional settings include `pollTimeoutSeconds`, `requestTimeoutSeconds`,
`historyMessages`, `additionalEnvVars`, `resources`, `nodeSelector`,
`tolerations`, `affinity`, and `podAnnotations`.
