import logging
from dataclasses import dataclass
from typing import List, Optional, Union

import openai
from autoevals import LLMClassifier, init
from braintrust import Span, SpanTypeAttribute
from braintrust.oai import wrap_openai

from tests.llm.utils.test_case_utils import _model_list_exists, create_eval_llm
from tests.llm.utils.test_env_vars import (
    AZURE_API_BASE,
    AZURE_API_KEY,
    AZURE_API_VERSION,
    CLASSIFIER_MODEL,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENROUTER_API_BASE,
    OPENROUTER_API_KEY,
)


@dataclass
class ClassifierModelParams:
    model: str
    api_key: Optional[str]
    api_base: Optional[str]
    api_version: Optional[str]

    @property
    def is_azure(self) -> bool:
        return bool(self.api_base and self.api_version)


def get_classifier_model_params() -> ClassifierModelParams:
    """Get classifier model parameters from model list or environment variables."""
    if _model_list_exists():
        llm = create_eval_llm(CLASSIFIER_MODEL)
        model_for_api = llm.model
        client_api_key = llm.api_key
        client_base_url = llm.api_base
        client_api_version = llm.api_version
    else:
        if not OPENAI_API_KEY and not AZURE_API_KEY and not OPENROUTER_API_KEY:
            raise ValueError(
                "No API key found (AZURE_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY)"
            )
        model_for_api = CLASSIFIER_MODEL
        if AZURE_API_BASE:
            client_api_key = AZURE_API_KEY
            client_base_url = AZURE_API_BASE
        elif OPENAI_API_KEY:
            client_api_key = OPENAI_API_KEY
            client_base_url = OPENAI_API_BASE
        else:
            client_api_key = OPENROUTER_API_KEY
            client_base_url = OPENROUTER_API_BASE
        client_api_version = AZURE_API_VERSION

        if AZURE_API_BASE and CLASSIFIER_MODEL.startswith("azure"):
            if len(CLASSIFIER_MODEL.split("/")) != 2:
                raise ValueError(
                    f"Current classifier model '{CLASSIFIER_MODEL}' does not meet the pattern 'azure/<deployment-name>' when using Azure OpenAI."
                )
            model_for_api = CLASSIFIER_MODEL.split("/", 1)[1]
        elif AZURE_API_BASE:
            model_for_api = CLASSIFIER_MODEL

    return ClassifierModelParams(
        model=model_for_api,
        api_key=client_api_key,
        api_base=client_base_url,
        api_version=client_api_version,
    )


classifier_model = CLASSIFIER_MODEL


def create_llm_client():
    """Create OpenAI/Azure client with same logic used by tests"""
    params = get_classifier_model_params()

    if params.is_azure:
        deployment = (
            params.model.split("/", 1)[1] if "/" in params.model else params.model
        )
        if not params.api_key:
            raise ValueError("No AZURE_API_KEY")
        client = openai.AzureOpenAI(
            azure_endpoint=params.api_base,
            azure_deployment=deployment,
            api_version=params.api_version,
            api_key=params.api_key,
        )
        model_for_api = deployment
    else:
        if not params.api_key:
            raise ValueError("No OPENAI_API_KEY or OPENROUTER_API_KEY")
        client = openai.OpenAI(api_key=params.api_key, base_url=params.api_base)
        model_for_api = params.model

    return client, model_for_api


# Register client with autoevals
try:
    client, _ = create_llm_client()
    params = get_classifier_model_params()
    if params.is_azure:
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
    params = get_classifier_model_params()
    if params.is_azure:
        logger.info(
            f"Evaluating correctness with Azure OpenAI; base_url={params.api_base}, api_version={params.api_version}, model={params.model}, api_key ending with: {params.api_key[-4:] if params.api_key else None}"
        )
        logger.info(
            "To use OpenAI instead, unset the environment variable AZURE_API_BASE"
        )
    else:
        logger.info(
            f"Evaluating correctness with OpenAI; model={params.model}, api_key ending with: {params.api_key[-4:] if params.api_key else None}"
        )
        logger.info(
            "To use Azure OpenAI instead, set the environment variables AZURE_API_BASE, AZURE_API_VERSION, and AZURE_API_KEY"
        )

    classifier = LLMClassifier(
        name="Correctness",
        prompt_template=prompt_prefix,
        choice_scores={"A": 1, "B": 0},
        use_cot=True,
        model=params.model,
        api_key=params.api_key if not params.is_azure else None,
        base_url=params.api_base if not params.is_azure else None,
        api_version=params.api_version if not params.is_azure else None,
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
