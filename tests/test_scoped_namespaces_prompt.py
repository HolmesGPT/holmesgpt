"""The SCOPED_NAMESPACES env var (set by the helm chart when namespaceScopedRBAC is on)
injects namespace-scope instructions into the system prompt, so Holmes doesn't waste
tool calls discovering its RBAC scope from Forbidden errors."""

from holmes.core.prompt import PromptComponent, build_system_prompt, get_scoped_namespaces


def _build_prompt(prompt_component_overrides=None) -> str:
    prompt = build_system_prompt(
        toolsets=[],
        skills=None,
        system_prompt_additions=None,
        cluster_name=None,
        ask_user_enabled=False,
        prompt_component_overrides=prompt_component_overrides or {},
    )
    assert prompt is not None
    return prompt


def test_get_scoped_namespaces_parsing(monkeypatch):
    monkeypatch.delenv("SCOPED_NAMESPACES", raising=False)
    assert get_scoped_namespaces() == []

    monkeypatch.setenv("SCOPED_NAMESPACES", "ns1")
    assert get_scoped_namespaces() == ["ns1"]

    monkeypatch.setenv("SCOPED_NAMESPACES", " ns1, ns2 ,,")
    assert get_scoped_namespaces() == ["ns1", "ns2"]


def test_system_prompt_includes_scope_instructions(monkeypatch):
    monkeypatch.setenv("SCOPED_NAMESPACES", "ns1")
    prompt = _build_prompt()
    assert "Namespace-scoped access" in prompt
    assert '"ns1"' in prompt
    assert "Never run cluster-wide queries" in prompt


def test_system_prompt_lists_multiple_namespaces(monkeypatch):
    monkeypatch.setenv("SCOPED_NAMESPACES", "ns1,ns2")
    prompt = _build_prompt()
    assert '"ns1", "ns2"' in prompt
    assert "one of these namespaces" in prompt


def test_system_prompt_has_no_scope_instructions_by_default(monkeypatch):
    monkeypatch.delenv("SCOPED_NAMESPACES", raising=False)
    prompt = _build_prompt()
    assert "Namespace-scoped access" not in prompt


def test_scope_instructions_survive_intro_disabled(monkeypatch):
    # the scope block must not be nested inside the intro component - disabling intro
    # must not silently drop the RBAC restrictions from the prompt
    monkeypatch.setenv("SCOPED_NAMESPACES", "ns1")
    prompt = _build_prompt({PromptComponent.INTRO: False})
    assert "You are HolmesGPT" not in prompt
    assert "Namespace-scoped access" in prompt
    assert '"ns1"' in prompt
