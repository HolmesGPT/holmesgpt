from typing import List, Optional, Union
from autoevals import LLMClassifier, init
from braintrust.oai import wrap_openai
import openai
import os
from braintrust import Span, SpanTypeAttribute

import logging

classifier_model = os.environ.get("CLASSIFIER_MODEL", os.environ.get("MODEL", "gpt-4o"))
api_key = os.environ.get("OPENAI_API_KEY", None)
azure_api_key = os.environ.get("AZURE_API_KEY", None)
base_url = os.environ.get("AZURE_API_BASE", None)
api_version = os.environ.get("AZURE_API_VERSION", None)
# Support for KAITO/custom OpenAI-compatible endpoints
openai_api_base = os.environ.get("OPENAI_API_BASE", None)
# Support for separate classifier endpoint
classifier_openai_api_base = os.environ.get("CLASSIFIER_OPENAI_API_BASE", openai_api_base)
classifier_api_key = os.environ.get("CLASSIFIER_OPENAI_API_KEY", api_key)


def create_llm_client():
    """Create OpenAI/Azure client with same logic used by tests"""
    # Handle KAITO/custom OpenAI-compatible endpoints
    if classifier_openai_api_base and classifier_openai_api_base != "https://api.openai.com/v1":
        if not classifier_api_key:
            raise ValueError("No CLASSIFIER_OPENAI_API_KEY (even dummy key required for custom endpoints)")
        client = openai.OpenAI(
            api_key=classifier_api_key,
            base_url=classifier_openai_api_base
        )
        # For KAITO/custom endpoints, strip provider prefix from model name
        model_for_api = classifier_model
        if model_for_api.startswith("openai/"):
            model_for_api = model_for_api[7:]  # Remove "openai/" prefix
        return client, model_for_api
    
    # Original logic for OpenAI and Azure
    if not api_key and not azure_api_key:
        raise ValueError("No API key found (AZURE_API_KEY or OPENAI_API_KEY)")

    if base_url:
        if not azure_api_key:
            raise ValueError("No AZURE_API_KEY")
        if classifier_model.startswith("azure"):
            if len(classifier_model.split("/")) != 2:
                raise ValueError(
                    f"Current classifier model '{classifier_model}' does not meet the pattern 'azure/<deployment-name>' when using Azure OpenAI."
                )
            deployment = classifier_model.split("/", 1)[1]
        else:
            deployment = classifier_model

        client = openai.AzureOpenAI(
            azure_endpoint=base_url,
            azure_deployment=deployment,
            api_version=api_version,
            api_key=azure_api_key,
        )
        # For Azure, return the deployment name for API calls
        model_for_api = deployment
    else:
        if not api_key:
            raise ValueError("No OPENAI_API_KEY")
        client = openai.OpenAI(api_key=api_key)
        # For OpenAI, return the full model name
        model_for_api = classifier_model

    return client, model_for_api


# Register client with autoevals
try:
    client, _ = create_llm_client()
    if base_url:
        wrapped = wrap_openai(client)
        init(wrapped)  # type: ignore
except Exception:
    # If client creation fails, individual tests will be skipped due to the fixture, so client = None is OK
    client = None


def evaluate_correctness(
    expected_elements: Union[str, List[str]],
    output: Optional[str],
    parent_span: Optional[Span],
    caplog,
    evaluation_type: str = "strict",
):
    expected_elements_str = "\n- ".join(expected_elements)

    caplog.set_level("INFO", logger="classifier")
    logger = logging.getLogger("classifier")

    if isinstance(expected_elements, str):
        expected_elements = [expected_elements]
    expected_elements_str = "\n- ".join(expected_elements)
    
    # Use the same API configuration logic as create_llm_client
    # Support both Azure (AZURE_API_BASE) and KAITO/custom (OPENAI_API_BASE) endpoints
    # Use classifier-specific endpoint if provided, otherwise fall back to main endpoint
    effective_base_url = base_url or classifier_openai_api_base
    effective_api_key = azure_api_key if base_url else classifier_api_key
    
    # For KAITO endpoints, strip the "openai/" prefix from model name if present
    effective_classifier_model = classifier_model
    if classifier_openai_api_base and effective_classifier_model.startswith("openai/"):
        effective_classifier_model = effective_classifier_model.replace("openai/", "", 1)

    prompt_prefix = """
You are evaluating the correctness of an OUTPUT given by a LLM. You must return a score that
represents the correctness of that OUTPUT.

The correctness is defined by the presence of EXPECTED ELEMENTS in the OUTPUT.
Make a judgement call whether each ELEMENT sufficiently matches the OUTPUT. ELEMENTS do
not need to appear verbatim or be a perfect match but their essence should be
present in the whole OUTPUT, even if it spans multiple sentences.

# EXPECTED ELEMENTS

- {{expected}}

# OUTPUT

{{output}}


Return a choice based on the number of EXPECTED ELEMENTS present in the OUTPUT.
Possible choices:
- A: All elements are presents
- B: Either no element is present or only some but not all elements are present
"""

    if evaluation_type == "loose":
        prompt_prefix = """
You are evaluating the correctness of an OUTPUT given by a LLM. You must return a score that
represents the correctness of that OUTPUT.

The correctness is defined by the presence of EXPECTED in the OUTPUT.
Make a judgement call whether each ELEMENT sufficiently matches the OUTPUT. ELEMENTS do
not need to appear verbatim or be a perfect match but their essence should be
present in the whole OUTPUT, even if it spans multiple sentences.

# EXPECTED

{{expected}}

# OUTPUT

{{output}}


Return a choice based on the number of EXPECTED presence in the OUTPUT.
Possible choices:
- A: The OUTPUT reasonably matches the EXPECTED content
- B: The OUTPUT does not match the EXPECTED content
"""
    if effective_base_url:
        if base_url:  # Azure endpoint
            logger.info(
                f"Evaluating correctness with Azure OpenAI; base_url={effective_base_url}, api_version={api_version}, model={effective_classifier_model}, api_key ending with: {effective_api_key[-4:] if effective_api_key else None}"
            )
            logger.info(
                "To use OpenAI instead, unset the environment variable AZURE_API_BASE"
            )
        else:  # KAITO/custom OpenAI-compatible endpoint
            logger.info(
                f"Evaluating correctness with KAITO endpoint; base_url={effective_base_url}, model={effective_classifier_model}, api_key ending with: {effective_api_key[-4:] if effective_api_key else None}"
            )
    else:
        logger.info(
            f"Evaluating correctness with OpenAI; model={effective_classifier_model}, api_key ending with: {effective_api_key[-4:] if effective_api_key else None}"
        )
        logger.info(
            "To use Azure OpenAI instead, set the environment variables AZURE_API_BASE, AZURE_API_VERSION, and AZURE_API_KEY"
        )

    classifier = LLMClassifier(
        name="Correctness",
        prompt_template=prompt_prefix,
        choice_scores={"A": 1, "B": 0},
        use_cot=True,
        model=effective_classifier_model,
        api_key=effective_api_key,
        base_url=effective_base_url,
        api_version=api_version,
    )
    if parent_span:
        with parent_span.start_span(
            name="Correctness", type=SpanTypeAttribute.SCORE
        ) as span:
            correctness_eval = classifier(
                input=prompt_prefix, output=output, expected=expected_elements_str
            )

            span.log(
                input=prompt_prefix,
                output=correctness_eval.metadata.get("rationale", ""),
                expected=expected_elements_str,
                scores={
                    "correctness": correctness_eval.score,
                },
                metadata=correctness_eval.metadata,
            )
            return correctness_eval
    else:
        return classifier(
            input=prompt_prefix, output=output, expected=expected_elements_str
        )


def evaluate_sections(
    sections: dict[str, bool], output: Optional[str], parent_span: Optional[Span]
):
    expected_sections = [section for section, expected in sections.items() if expected]
    expected_sections_str = "\n".join([f"- {section}" for section in expected_sections])
    if not expected_sections_str:
        expected_sections_str = "<No section is expected>"

    unexpected_sections = [
        section for section, expected in sections.items() if not expected
    ]
    unexpected_sections_str = "\n".join(
        [f"- {section}" for section in unexpected_sections]
    )
    if not unexpected_sections_str:
        unexpected_sections_str = "<No element>"

    prompt_prefix = """
You are evaluating the correctness of an OUTPUT given by a LLM. You must return a score that
represents the correctness of that OUTPUT.

The LLM output is expected to be split into sections. Typically each section is represented by a markdown title `# <section title>`.
Some sections are expected and should be populated in the output. Some sections are unexpected and should not be present in the outpout
(i.e. there is no such title: `# <unexpected section`)

If there are <No element> in EXPECTED SECTIONS assume the OUTPUT has all appropriate EXPECTED SECTIONS.
If there are <No element> in UNEXPECTED SECTIONS assume the OUTPUT has no UNEXPECTED SECTIONS.


# EXPECTED SECTIONS

{{expected}}


# UNEXPECTED SECTIONS

{{input}}


# OUTPUT

{{output}}


Return a choice based on the number of EXPECTED ELEMENTS present in the OUTPUT.
Possible choices:
A. One or more of the EXPECTED SECTIONS is missing and one or more of the UNEXPECTED SECTIONS is present
B. All EXPECTED SECTIONS are present in the OUTPUT and no UNEXPECTED SECTIONS is present in the output
"""

    classifier = LLMClassifier(
        name="sections",
        prompt_template=prompt_prefix,
        choice_scores={"A": 0, "B": 1},
        use_cot=True,
        model=classifier_model,
    )
    if parent_span:
        with parent_span.start_span(
            name="Sections", type=SpanTypeAttribute.SCORE
        ) as span:
            correctness_eval = classifier(
                input=unexpected_sections_str,
                output=output,
                expected=expected_sections_str,
            )

            span.log(
                input=prompt_prefix,
                output=correctness_eval.metadata.get("rationale", ""),
                expected=expected_sections_str,
                scores={
                    "sections": correctness_eval.score,
                },
                metadata=correctness_eval.metadata,
            )

            return correctness_eval
    else:
        return classifier(
            input=unexpected_sections_str, output=output, expected=expected_sections_str
        )
