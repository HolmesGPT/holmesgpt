from typing import Optional
from urllib.parse import quote

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
