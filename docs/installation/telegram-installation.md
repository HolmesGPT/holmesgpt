# Telegram Bot

Chat with HolmesGPT from Telegram and, optionally, receive **scheduled health checks** that are pushed to you only when something is wrong.

The bot long-polls Telegram, so it only makes **outbound** HTTPS calls: no inbound port, ingress or public URL is needed. It talks to a running Holmes server through `POST /api/chat`.

## 1. Create the bot

1. Talk to [@BotFather](https://t.me/BotFather), run `/newbot` and keep the token.
2. Start the bot once and send `/id`: it answers with your chat id even if you are not allowed yet.

## 2. Run it

```bash
export TELEGRAM_BOT_TOKEN=123456:ABC...        # from BotFather
export TELEGRAM_ALLOWED_CHAT_IDS=111111111     # comma separated, required
export HOLMES_URL=http://holmes:80             # default http://localhost:8080
holmes-telegram
```

Only **private** chats whose id is in `TELEGRAM_ALLOWED_CHAT_IDS` are served. Everyone else only gets their own chat id (once every 10 minutes) and is never forwarded to Holmes. Message contents are never logged. The bot refuses to start with an empty allow-list.

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | required | Bot token |
| `TELEGRAM_ALLOWED_CHAT_IDS` | required | Allowed private chat ids |
| `HOLMES_URL` | `http://localhost:8080` | Holmes server |
| `HOLMES_API_KEY` | empty | Sent as `Authorization: Bearer` |
| `TELEGRAM_CHECKS_DIR` | empty | Enables scheduled checks (see below) |
| `TELEGRAM_REQUEST_TIMEOUT` | `300` | Seconds to wait for a chat answer |
| `TELEGRAM_CHECK_TIMEOUT` | `900` | Seconds to wait for a check answer |
| `TELEGRAM_MAX_HISTORY_BYTES` | `300000` | Conversation is reset above this size |

Commands: any text is a question, `/reset` clears the conversation, `/id` shows the chat id, `/help`.

### In Kubernetes

The bot is a plain client of the Holmes API, so it runs as a normal Deployment next to Holmes:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: holmes-telegram
spec:
  replicas: 1            # one poller per bot token: Telegram rejects concurrent getUpdates
  selector: {matchLabels: {app: holmes-telegram}}
  template:
    metadata: {labels: {app: holmes-telegram}}
    spec:
      containers:
        - name: bot
          image: robustadev/holmes:latest   # use the same version as your Holmes release
          command: ["holmes-telegram"]
          env:
            - {name: HOLMES_URL, value: "http://holmes-holmes.holmes.svc"}
          envFrom:
            - secretRef: {name: holmes-telegram}   # TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS
```

## 3. Scheduled health checks (optional)

Off by default. Set `TELEGRAM_CHECKS_DIR` to a directory with a `schedule.json` and one prompt file per check. A ready-to-copy example (nodes, pods, events) is in `holmes/plugins/telegram/example_checks/`.

```json
{
  "timezone": "Europe/Rome",
  "batches": ["08:30", "20:30"],
  "spacing_minutes": 5,
  "checks": [{"name": "cluster-pods", "file": "pods.txt"}]
}
```

- Check *i* of every batch runs at `batch + i * spacing_minutes` in `timezone` (DST aware). Checks never run concurrently: each one is a full tool loop.
- The prompt file says **what** to verify and when the result is not OK. The bot appends the response contract: the answer must start with `<emoji> <SEVERITY> - <check name>` where severity is `OK`, `WARNING`, `CRITICAL` or `UNKNOWN`.
- **OK stays silent.** Anything else is sent to every allowed chat. Because a silent bot must not hide a broken check, an HTTP error, an answer without a severity header, or an answer produced with **zero tool calls** is reported as `UNKNOWN`, never as OK. A failed call is retried once with a prompt that asks for a shorter investigation.
- `/check` lists the checks with their last result; `/check <name>` runs one now and always replies.
- Put site-specific known issues (noisy but harmless errors) in your own prompt files; the examples are generic on purpose.

Checks use your Holmes toolsets (Kubernetes, Prometheus, ...) read-only, and each run costs LLM tokens: 20-90 tool calls and 1-4 minutes is typical, so keep the number of batches small.
