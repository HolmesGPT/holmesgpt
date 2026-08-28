from typing import Optional
from urllib.parse import quote, urlparse

from holmes.common.env_vars import ROBUSTA_UI_DOMAIN

# The request_source of an interactive Ask Holmes chat started in the platform
# UI — the only conversation kind with a /holmes/chat page to link back to.
FREEFORM_CHAT_REQUEST_SOURCE = "freeform"


def derive_freeform_chat_link(
    request_source: Optional[str],
    conversation_id: Optional[str],
    account_id: Optional[str],
) -> Optional[str]:
    """Server-derived platform URL of a freeform Ask Holmes chat.

    Built account-less with an ``?account_id=`` hint (the SPA's account guard
    resolves it into the account's route), so it needs nothing beyond what the
    server already knows — no client-supplied value can pick the destination.
    Relay's UI-link builders emit the other canonical shape for the same page,
    ``/<account_name>/holmes/chat/<id>``, because relay can resolve the account
    name; both route to the same chat, so don't "unify" one onto the other.
    """
    if request_source != FREEFORM_CHAT_REQUEST_SOURCE:
        return None
    if not conversation_id or not account_id:
        return None
    base = (ROBUSTA_UI_DOMAIN or "").rstrip("/")
    if not base:
        return None
    return (
        f"{base}/holmes/chat/{quote(str(conversation_id), safe='')}"
        f"?account_id={quote(str(account_id), safe='')}"
    )


MAX_CONVERSATION_LINK_LENGTH = 2048


def _is_allowed_conversation_link_origin(scheme: str, host: str) -> bool:
    # The deployment's own UI (covers self-hosted instances, http included).
    ui = urlparse(ROBUSTA_UI_DOMAIN or "")
    if scheme == ui.scheme and host == (ui.hostname or "").lower():
        return True
    if scheme != "https":
        return False
    # Robusta platform in any region, Slack permalinks, Teams deep links —
    # the only surfaces that originate conversations. Slack permalinks always
    # live on a workspace subdomain, so apex slack.com is deliberately absent.
    return (
        host == "robusta.dev"
        or host.endswith(".robusta.dev")
        or host.endswith(".slack.com")
        or host == "teams.microsoft.com"
    )


def sanitize_conversation_link(link: Optional[str]) -> Optional[str]:
    """Drop any conversation_link that isn't a well-formed URL to a surface
    conversations actually originate from.

    The value is client-suppliable (REST body, Conversations metadata) and is
    rendered verbatim into the system prompt with an instruction to copy it
    into PR/issue descriptions — so both the text shape AND the destination
    must be server-controlled: no whitespace/control-character/length games
    (prompt injection), and no arbitrary hosts (a tracking or phishing URL
    laundered into public artifacts). All server-built links (Slack permalinks,
    Teams deep links, platform UI URLs, derive_freeform_chat_link's output)
    pass this check.
    """
    if not link:
        return None
    if len(link) > MAX_CONVERSATION_LINK_LENGTH or any(
        ch.isspace() or not ch.isprintable() for ch in link
    ):
        return None
    parsed = urlparse(link)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host.startswith(".") or not _is_allowed_conversation_link_origin(
        parsed.scheme, host
    ):
        return None
    return link
