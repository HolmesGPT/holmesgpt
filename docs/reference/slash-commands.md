# Slash Commands

Slash commands provide special actions in HolmesGPT's interactive mode. Type any command with a leading `/` to execute it.

## Built-in Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands and their descriptions |
| `/exit` | Exit interactive mode and return to shell |
| `/clear` | Clear conversation history and start fresh |
| `/tools` | Show available toolsets and their status |
| `/context` | Display token usage and context size information |
| `/auto` | Toggle automatic display of tool outputs |
| `/last` | Show tool outputs from the most recent AI response |
| `/show [number\|name]` | View specific tool output in scrollable modal |
| `/run <command>` | Execute shell command and optionally share with AI |
| `/shell` | Start interactive shell session |

## Command Details

### `/help`
Displays all available slash commands and their descriptions.

```
> /help
```

### `/exit`
Exits the interactive session and returns to your shell. Alternative: press `Ctrl+C` twice.

```
> /exit
```

### `/clear`
Clears all conversation history and starts fresh. Useful when switching topics or when context becomes too large.

```
> /clear
```

### `/tools`
Lists all configured toolsets and their current status (enabled/disabled).

```
> /tools
Available toolsets:
✓ kubernetes (enabled)
✓ prometheus (enabled)
✗ grafana (disabled - no URL configured)
```

### `/context`
Displays detailed information about token usage and context size.

```
> /context
Context Usage:
- System: 1,250 tokens
- User: 3,421 tokens
- Assistant: 2,156 tokens
- Tools: 5,234 tokens
Total: 12,061 / 128,000 tokens (9.4%)

Top tools by token usage:
1. kubernetes_get_pod_logs: 2,341 tokens
2. kubernetes_describe_pod: 1,893 tokens
```

### `/auto`
Controls whether tool outputs are automatically shown after each AI response. When disabled, use `/last` to view outputs.

```
> /auto
Auto-display tool outputs: ON
```

### `/last`
Displays all tool outputs from the most recent AI response. Useful when auto-display is off or to review outputs again.

```
> /last
```

### `/show [number|name]`
Opens a scrollable modal to view full tool output. You can specify outputs by:
- Number: `/show 3` (shows 3rd tool output)
- Name: `/show kubernetes_get_pod_logs` (shows specific tool output)

```
> /show 1
> /show kubernetes_get_pod_logs
```

#### Modal Navigation
- `j`/`k` or ↑/↓ - Move up/down
- `g`/`G` - Go to top/bottom
- `d`/`u` - Half page down/up
- `f`/`b` or PgDn/PgUp - Full page down/up
- `w` - Toggle word wrap
- `q` or Esc - Close modal

### `/run <command>`
Runs a shell command and optionally shares the output with the AI.

```
> /run kubectl get pods -n production
# Output displayed...
Share output with AI? (Y/n): y
Add a comment or question (optional): why are some pods in CrashLoopBackOff?
```

### `/shell`
Starts an interactive shell session. When you exit, you can share the entire session with the AI.

```
> /shell
$ kubectl logs failing-pod-xyz
$ kubectl describe pod failing-pod-xyz
$ exit
Share shell session with AI? (Y/n): y
Add a comment or question (optional): The pod keeps failing with OOM errors
```
