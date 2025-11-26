from typing import List, Optional, Union
from autoevals import LLMClassifier, init
from braintrust.oai import wrap_openai
import openai
import os
from braintrust import Span, SpanTypeAttribute
from holmes.common.env_vars import DEFAULT_MODEL
from tests.llm.utils.test_case_utils import create_eval_llm, _model_list_exists

import logging

classifier_model = os.environ.get(
    "CLASSIFIER_MODEL", os.environ.get("MODEL", DEFAULT_MODEL)
)
api_key = os.environ.get("OPENAI_API_KEY", None)
azure_api_key = os.environ.get("AZURE_API_KEY", None)
base_url = os.environ.get("AZURE_API_BASE", None)
api_version = os.environ.get("AZURE_API_VERSION", None)


def create_llm_client():
    """Create OpenAI/Azure client with same logic used by tests"""
    if _model_list_exists():
        llm = create_eval_llm(classifier_model)
        model_for_api = llm.model
        client_api_key = llm.api_key
        client_base_url = llm.api_base
        client_api_version = llm.api_version
    else:
        if not api_key and not azure_api_key:
            raise ValueError("No API key found (AZURE_API_KEY or OPENAI_API_KEY)")
        model_for_api = classifier_model
        client_api_key = azure_api_key if base_url else api_key
        client_base_url = base_url
        client_api_version = api_version
        
        if base_url and classifier_model.startswith("azure"):
            if len(classifier_model.split("/")) != 2:
                raise ValueError(
                    f"Current classifier model '{classifier_model}' does not meet the pattern 'azure/<deployment-name>' when using Azure OpenAI."
                )
            model_for_api = classifier_model.split("/", 1)[1]
        elif base_url:
            model_for_api = classifier_model
    
    is_azure = model_for_api.startswith("azure/") or (client_base_url and client_api_version)
    if is_azure:
        deployment = model_for_api.split("/", 1)[1] if "/" in model_for_api else model_for_api
        if not client_api_key:
            raise ValueError("No API key found for Azure client")
        if not client_base_url:
            raise ValueError("No base URL found for Azure client")
        client = openai.AzureOpenAI(
            azure_endpoint=client_base_url,
            azure_deployment=deployment,
            api_version=client_api_version,
            api_key=client_api_key,
        )
        return client, deployment
    else:
        if not client_api_key:
            raise ValueError("No API key found for OpenAI client")
        client = openai.OpenAI(api_key=client_api_key)
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
    if base_url:
        logger.info(
            f"Evaluating correctness with Azure OpenAI; base_url={base_url}, api_version={api_version}, model={classifier_model}, api_key ending with: {api_key[-4:] if api_key else None}"
        )
        logger.info(
            "To use OpenAI instead, unset the environment variable AZURE_API_BASE"
        )
    else:
        logger.info(
            f"Evaluating correctness with OpenAI; model={classifier_model}, api_key ending with: {api_key[-4:] if api_key else None}"
        )
        logger.info(
            "To use Azure OpenAI instead, set the environment variables AZURE_API_BASE, AZURE_API_VERSION, and AZURE_API_KEY"
        )

    classifier = LLMClassifier(
        name="Correctness",
        prompt_template=prompt_prefix,
        choice_scores={"A": 1, "B": 0},
        use_cot=True,
        model=classifier_model,
        api_key=api_key,
        base_url=base_url,
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
