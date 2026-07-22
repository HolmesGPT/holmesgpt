"""Per-cluster scoping of session-approved bash prefixes.

Approving a prefix while running a remote tool on one cluster must NOT
auto-approve it on another cluster (or locally), and vice versa — approvals
are isolated per (conversation, cluster). See tool_calling_llm._bash_prefix_scope
and extract_bash_session_prefixes_by_agent.
"""

import json

from holmes.core.tool_calling_llm import (
    _LOCAL_BASH_PREFIX_SCOPE,
    _bash_prefix_scope,
    extract_bash_session_prefixes_by_agent,
)


def _tool_msg(prefixes, agent=None):
    """A conversation 'tool' message carrying saved-prefix metadata, matching
    the on-wire format extract_bash_session_prefixes_by_agent parses."""
    meta = {"bash_session_approved_prefixes": prefixes}
    if agent is not None:
        meta["bash_session_approved_agent"] = agent
    return {"role": "tool", "content": f"result tool_call_metadata={json.dumps(meta)}"}


def test_scope_key_remote_uses_agent_local_uses_sentinel():
    assert _bash_prefix_scope("remote_bash", {"agent_name": "cluster-a"}) == "cluster-a"
    assert _bash_prefix_scope("bash", {}) == _LOCAL_BASH_PREFIX_SCOPE
    # remote tool without an agent falls back to the local sentinel (never leaks)
    assert _bash_prefix_scope("remote_bash", {}) == _LOCAL_BASH_PREFIX_SCOPE


def test_prefixes_are_bucketed_per_agent():
    messages = [
        _tool_msg(["curl"], agent="cluster-a"),
        _tool_msg(["dig"], agent="cluster-b"),
        _tool_msg(["ls"]),  # local (no agent)
    ]
    by_agent = extract_bash_session_prefixes_by_agent(messages)

    assert by_agent.get("cluster-a") == ["curl"]
    assert by_agent.get("cluster-b") == ["dig"]
    assert by_agent.get(_LOCAL_BASH_PREFIX_SCOPE) == ["ls"]


def test_approval_on_a_does_not_apply_to_b_or_local():
    """The exact requirement: approve curl on A -> A auto-approves, B and local
    still require approval."""
    messages = [_tool_msg(["curl"], agent="cluster-a")]
    by_agent = extract_bash_session_prefixes_by_agent(messages)

    # A remembers curl:
    a_scope = _bash_prefix_scope("remote_bash", {"agent_name": "cluster-a"})
    assert "curl" in by_agent.get(a_scope, [])

    # B does not:
    b_scope = _bash_prefix_scope("remote_bash", {"agent_name": "cluster-b"})
    assert "curl" not in by_agent.get(b_scope, [])

    # local does not:
    assert "curl" not in by_agent.get(_LOCAL_BASH_PREFIX_SCOPE, [])


def test_legacy_metadata_without_agent_scopes_local_only():
    """Older conversations saved prefixes with no agent tag; they must scope to
    local only and never leak to a remote cluster."""
    messages = [_tool_msg(["curl"])]  # no agent key at all
    by_agent = extract_bash_session_prefixes_by_agent(messages)

    assert by_agent.get(_LOCAL_BASH_PREFIX_SCOPE) == ["curl"]
    assert by_agent.get("cluster-a", []) == []
