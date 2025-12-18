"""Tests to verify AI safety prompt is included in all system prompts."""

import pytest
from holmes.plugins.prompts import load_and_render_prompt


class TestAISafetyPromptInclusion:
    """Test that AI safety prompt is included in all main system prompt templates."""

    @pytest.mark.parametrize(
        "template_path",
        [
            "builtin://generic_ask.jinja2",
            "builtin://generic_ask_conversation.jinja2",
            "builtin://generic_ask_for_issue_conversation.jinja2",
            "builtin://kubernetes_workload_ask.jinja2",
            "builtin://generic_investigation.jinja2",
        ],
    )
    def test_ai_safety_prompt_included(self, template_path):
        """Test that AI safety prompt is included in system prompt templates."""
        # Basic context that all templates should support
        context = {
            "toolsets": [],
            "cluster_name": "test-cluster",
            "issue": {"source_type": "test"},  # for investigation template
            "investigation": "test investigation",  # for issue conversation template
            "tools_called_for_investigation": [],  # for issue conversation template
            "sections": {},  # for investigation template output format
        }

        rendered = load_and_render_prompt(template_path, context)

        # Check that key AI safety sections are present
        assert (
            "# Safety & Guardrails" in rendered
        ), f"AI safety header missing from {template_path}"
        assert (
            "## Content Harms" in rendered
        ), f"Content Harms section missing from {template_path}"
        assert (
            "## Jailbreaks – UPIA" in rendered
        ), f"UPIA section missing from {template_path}"
        assert (
            "## Jailbreaks – XPIA" in rendered
        ), f"XPIA section missing from {template_path}"
        assert (
            "## IP / Third-Party Content Regurgitation" in rendered
        ), f"IP section missing from {template_path}"
        assert (
            "## Ungrounded Content" in rendered
        ), f"Ungrounded Content section missing from {template_path}"

        # Check for key safety phrases
        assert (
            "non-negotiable" in rendered
        ), f"Non-negotiable clause missing from {template_path}"
        assert (
            "copyright laws" in rendered
        ), f"Copyright clause missing from {template_path}"
        assert (
            "physical or emotional harm" in rendered
        ), f"Harm prevention clause missing from {template_path}"


def test_ai_safety_template_exists():
    """Test that the AI safety template file exists and can be rendered."""
    rendered = load_and_render_prompt("builtin://_ai_safety.jinja2", {})

    # Should contain all expected sections
    assert "# Safety & Guardrails" in rendered
    assert "## Content Harms" in rendered
    assert "## Jailbreaks – UPIA" in rendered
    assert "## Jailbreaks – XPIA" in rendered
    assert "## IP / Third-Party Content Regurgitation" in rendered
    assert "## Ungrounded Content" in rendered
