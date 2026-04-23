#!/usr/bin/env python3
"""Mint a Microsoft Graph delegated access token for the native Teams toolset.

Uses MSAL device-code flow against the Microsoft Graph Command Line Tools
public client (Microsoft first-party, multi-tenant, no app registration
required). First run prompts you to sign in via a browser device-code URL;
subsequent runs silently refresh the cached refresh token so you don't
re-auth every hour.

Scopes requested (delegated):
    User.Read, User.ReadBasic.All,
    Chat.ReadWrite, ChatMessage.Send,
    Team.ReadBasic.All, Channel.Create, ChannelMessage.Send
Note: Channel.Create usually requires admin consent in enterprise tenants.

Cache location: ~/.holmes/teams_msal_cache.bin (0600). Contains the refresh
token (~90-day lifetime by default). Delete this file to force a fresh
device-code sign-in.

Usage (requires msal):
    pip install msal
    python scripts/get_teams_token.py
    # or capture into env:
    export TEAMS_AUTH_TOKEN=$(python scripts/get_teams_token.py)

Prints the access token to stdout; progress and prompts go to stderr.
"""
import os
import sys
from pathlib import Path

try:
    import msal
except ImportError:
    print(
        "msal not installed. Run: pip install msal",
        file=sys.stderr,
    )
    sys.exit(2)


CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"  # Microsoft Graph Command Line Tools
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = [
    "User.Read",
    "User.ReadBasic.All",
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "Team.ReadBasic.All",
    "Channel.Create",
    "ChannelMessage.Send",
]
CACHE_DIR = Path.home() / ".holmes"
CACHE_FILE = CACHE_DIR / "teams_msal_cache.bin"


def main() -> int:
    CACHE_DIR.mkdir(mode=0o700, exist_ok=True)

    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text())

    app = msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )

    result = None
    for account in app.get_accounts():
        result = app.acquire_token_silent(SCOPES, account=account)
        if result:
            break

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            print("Device flow init failed:", flow, file=sys.stderr)
            return 1
        print(flow["message"], file=sys.stderr)
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print(
            "Auth failed:",
            result.get("error_description", result),
            file=sys.stderr,
        )
        return 1

    if cache.has_state_changed:
        CACHE_FILE.write_text(cache.serialize())
        os.chmod(CACHE_FILE, 0o600)

    print(result["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
