# Microsoft Teams Bot Permissions

This document explains how the HolmesGPT Microsoft Teams bot handles permissions and what permissions it needs to work effectively and provide the best results.

## How the HolmesGPT Teams Bot Works

The bot is **entirely user-initiated**—it only activates when you explicitly `@mention` it with a troubleshooting question. When you ask HolmesGPT a question in Teams (e.g., "`@HolmesGPT` why is my pod crashing?"), the bot:

1. **Reads your message and conversation context** (using RSC permissions) to understand your request  
2. **Uses AI to analyze and investigate your query** to identify potential root causes across your cloud environment  
3. **Saves detailed tool outputs** (logs, API responses) to SharePoint for easy sharing (using OAuth permissions)  
4. **Replies in Teams** with well-formatted results summarizing the investigation findings  

The bot does not passively monitor channels, scan messages in the background, or access conversations where it hasn't been explicitly invoked.

The bot uses three authentication methods:

| Operation | Auth Method | Why |
|-----------|-------------|-----|
| Reading troubleshooting questions from Teams | RSC (Resource-Specific Consent) | Works in any team where bot is installed, no user membership required |
| Sending and updating bot responses | Bot Framework Connector API | Standard bot capability for replying to users |
| Saving tool call outputs to SharePoint | Delegated (OAuth) | RSC doesn't support SharePoint operations |

## Permissions Requested and Why

### RSC (Resource-Specific Consent) Permissions

RSC permissions are automatically granted when a team owner/admin adds the bot to their team. These are **scoped permissions**—they only apply to the specific team or chat where the bot is installed, not tenant-wide.

- **`ChannelMessage.Read.Group`** - Read messages in channels where the bot is installed
    - Used **only when a user `@mentions` the bot** to read the question and relevant conversation context
    - Enables context gathering: when an alert is posted in a channel and a user tags HolmesGPT to investigate, the bot reads the alert message and thread to understand what to investigate
    - Read-only access to messages; the bot cannot modify or delete messages

- **`ChatMessage.Read.Chat`** - Read messages in chats where the bot is added
    - Used **only when a user `@mentions` the bot** in direct or group chats
    - Enables context gathering: when a user shares an error or alert in chat and asks HolmesGPT to investigate, the bot reads the conversation to understand the context
    - Read-only access to other users' messages; the bot cannot modify or delete other users' messages

### Bot Framework Permissions

The bot sends and updates its own response messages using the **Bot Framework Connector API** (not Microsoft Graph). This is a standard capability granted to all registered Teams bots and allows the bot to:

- Send reply messages to users who `@mention` it
- Update its own messages in real-time (e.g., streaming investigation progress)
- The bot cannot modify or delete other users' messages

### Delegated (OAuth) Permissions

Delegated permissions require an admin to complete a one-time OAuth flow. These permissions act on behalf of the authenticated user.

- **`User.Read`** - Read the authenticated user's basic profile
    - Used only for authentication validation during OAuth setup

- **`Chat.Read`** - Fallback for reading chat messages
    - Used only when RSC permissions are unavailable (rare edge case)
    - Allows the bot to read conversation context when a user `@mentions` it to investigate an issue (e.g., when an alert is posted in a channel and a user tags HolmesGPT, the bot can read the alert message to understand what to investigate)
    - Read-only; cannot modify messages

- **`Sites.ReadWrite.All`** - Save investigation outputs to SharePoint
    - **Read operations**: Used solely to discover the team's SharePoint document library location (site URL, drive ID, folder structure)
    - **Write operations**: Create investigation log files containing kubectl outputs, Prometheus query results, API responses, and other diagnostic data
    - Files are created in the team's SharePoint Documents library for easy sharing and collaboration
    - The bot does not read, modify, or delete existing SharePoint content

- **`offline_access`** - Maintain authentication session
    - Allows the bot to save investigation outputs without requiring re-authentication for each request

## Security Summary

### What the Bot Does

| Action | When | Permission Used |
|--------|------|-----------------|
| Read user's troubleshooting question | Only when user @mentions bot | `ChannelMessage.Read.Group` / `ChatMessage.Read.Chat` (RSC) |
| Read conversation context | Only when user `@mentions` bot | `ChannelMessage.Read.Group` / `ChatMessage.Read.Chat` (RSC) |
| Send and update bot's own response messages | Only when replying to user | Bot Framework Connector API |
| Discover SharePoint document library location | Only when saving investigation results | `Sites.ReadWrite.All` (read portion) |
| Create investigation log file | Only when saving investigation results | `Sites.ReadWrite.All` (write portion) |

### What the Bot Does NOT Do

- Does not passively monitor or scan conversations
- Does not access messages unless explicitly `@mentioned`
- Does not modify, edit, or delete other users' messages (only its own responses)
- Does not access Teams outside of where the bot is installed (RSC is scoped)
- Does not read, modify, or delete existing SharePoint files

### Data Flow

```
User @mentions bot → Bot investigates issue →
Bot generates response → Bot creates SharePoint investigation files → Bot replies in Teams
```

All message access is synchronous and user-initiated. The bot does not maintain persistent access to conversation history.

## Related Documentation

- [Microsoft Graph API Permissions Reference](https://learn.microsoft.com/en-us/graph/permissions-reference) - Official Microsoft documentation
