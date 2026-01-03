import logging
from typing import Optional

from holmes.config import Config
from holmes.core.investigation_structured_output import (
    DEFAULT_SECTIONS,
    REQUEST_STRUCTURED_OUTPUT_FROM_LLM,
    get_output_format_for_investigation,
    process_response_into_sections,
)
from holmes.core.issue import Issue
from holmes.core.models import InvestigateRequest, InvestigationResult
from holmes.core.prompt import generate_user_prompt
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tracing import DummySpan, SpanType
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.runbooks import RunbookCatalog
from holmes.utils import sentry_helper
from holmes.utils.global_instructions import generate_runbooks_args


def investigate_issues(
    investigate_request: InvestigateRequest,
    dal: SupabaseDal,
    config: Config,
    model: Optional[str] = None,
    trace_span=DummySpan(),
    runbooks: Optional[RunbookCatalog] = None,
) -> InvestigationResult:
    context = dal.get_issue_data(investigate_request.context.get("robusta_issue_id"))

    global_instructions = dal.get_global_instructions_for_account()

    raw_data = investigate_request.model_dump()
    if context:
        raw_data["extra_context"] = context

    # If config is not preinitilized
    create_issue_investigator_span = trace_span.start_span(
        "create_issue_investigator", SpanType.FUNCTION.value
    )
    ai = config.create_issue_investigator(dal=dal, model=model)
    create_issue_investigator_span.end()

    issue = Issue(
        id=context["id"] if context else "",
        name=investigate_request.title,
        source_type=investigate_request.source,
        source_instance_id=investigate_request.source_instance_id,
        raw=raw_data,
    )

    investigation = ai.investigate(
        issue,
        prompt=investigate_request.prompt_template,
        global_instructions=global_instructions,
        sections=investigate_request.sections,
        trace_span=trace_span,
        runbooks=runbooks,
    )

    (text_response, sections) = process_response_into_sections(investigation.result)

    if sections is None:
        sentry_helper.capture_sections_none(content=investigation.result)

    logging.debug(f"text response: {text_response}")
    return InvestigationResult(
        analysis=text_response,
        sections=sections,
        tool_calls=investigation.tool_calls or [],
        num_llm_calls=investigation.num_llm_calls,
        instructions=investigation.instructions,
        metadata=investigation.metadata,
    )


def get_investigation_context(
    investigate_request: InvestigateRequest,
    dal: SupabaseDal,
    config: Config,
    request_structured_output_from_llm: Optional[bool] = None,
):
    ai = config.create_issue_investigator(dal=dal, model=investigate_request.model)

    raw_data = investigate_request.model_dump()
    context = dal.get_issue_data(investigate_request.context.get("robusta_issue_id"))
    if context:
        raw_data["extra_context"] = context

    issue = Issue(
        id=context["id"] if context else "",
        name=investigate_request.title,
        source_type=investigate_request.source,
        source_instance_id=investigate_request.source_instance_id,
        raw=raw_data,
    )

    issue_instructions = ai.runbook_manager.get_instructions_for_issue(issue)

    # This section is about setting vars to request the LLM to return structured output.
    # It does not mean that Holmes will not return structured sections for investigation as it is
    # capable of splitting the markdown into sections
    if request_structured_output_from_llm is None:
        request_structured_output_from_llm = REQUEST_STRUCTURED_OUTPUT_FROM_LLM
    response_format = None
    sections = investigate_request.sections
    if not sections:
        sections = DEFAULT_SECTIONS
        request_structured_output_from_llm = False
        logging.info(
            "No section received from the client. Default sections will be used."
        )
    elif ai.llm.model and ai.llm.model.startswith(("bedrock", "gemini")):
        # Structured output does not work well with Bedrock Anthropic Sonnet 3.5, or gemini through litellm
        request_structured_output_from_llm = False

    if request_structured_output_from_llm:
        response_format = get_output_format_for_investigation(sections)
        logging.info("Structured output is enabled for this request")
    else:
        logging.info("Structured output is disabled for this request")

    runbook_catalog = config.get_runbook_catalog()
    system_prompt = load_and_render_prompt(
        investigate_request.prompt_template,
        {
            "issue": issue,
            "sections": sections,
            "structured_output": request_structured_output_from_llm,
            "toolsets": ai.tool_executor.toolsets,
            "cluster_name": config.cluster_name,
            "runbooks_enabled": True if runbook_catalog else False,
        },
    )
    base_user = ""

    global_instructions = dal.get_global_instructions_for_account()
    runbooks_ctx = generate_runbooks_args(
        runbook_catalog=runbook_catalog,
        global_instructions=global_instructions,
        issue_instructions=issue_instructions,
    )

    base_user = f"{base_user}\n #This is context from the issue:\n{issue.raw}"

    user_prompt = generate_user_prompt(
        base_user,
        runbooks_ctx,
    )

    return ai, system_prompt, user_prompt, response_format, sections, issue_instructions
