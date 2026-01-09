from typing import TYPE_CHECKING, Dict, List, Optional

from pydantic import BaseModel

from holmes.plugins.runbooks import RunbookCatalog

if TYPE_CHECKING:
    from holmes.core.resource_instruction import ResourceInstructions


class Instructions(BaseModel):
    instructions: List[str] = []


def _format_instructions_block(
    items: List[str], header: str = "My instructions to check:"
) -> str:
    lines = [f"* {s}" for s in items if isinstance(s, str) and s.strip()]
    if not lines:
        return ""
    bullets = "\n".join(lines) + "\n"
    return f"{header}\n{bullets}"


def _format_resource_instructions(
    resource_instructions: Optional["ResourceInstructions"],
) -> List[str]:  # type: ignore
    items = []
    if resource_instructions is not None:
        if getattr(resource_instructions, "instructions", None):
            items.extend(resource_instructions.instructions)
        if getattr(resource_instructions, "documents", None):
            for document in resource_instructions.documents:
                items.append(f"fetch information from this URL: {document.url}")
    return items


def generate_runbooks_args(
    runbook_catalog: Optional[RunbookCatalog],
    global_instructions: Optional[Instructions] = None,
    issue_instructions: Optional[List[str]] = None,
    resource_instructions: Optional["ResourceInstructions"] = None,  # type: ignore
) -> Dict[str, str]:
    catalog_str = runbook_catalog.to_prompt_string() if runbook_catalog else ""

    combined_instructions = []
    if issue_instructions:
        combined_instructions.extend(issue_instructions)
    combined_instructions.extend(_format_resource_instructions(resource_instructions))
    issue_block = (
        _format_instructions_block(combined_instructions)
        if combined_instructions
        else ""
    )

    gi_list = getattr(global_instructions, "instructions", None) or []
    global_block = (
        _format_instructions_block(
            [s for s in gi_list if isinstance(s, str)], header=""
        )
        if gi_list
        else ""
    )

    return {
        "runbook_catalog": catalog_str,
        "custom_instructions": issue_block,
        "global_instructions": global_block,
    }
